"""MetricsMonitor — background async task that collects system metrics."""
from __future__ import annotations

import asyncio
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SystemSnapshot:
    """Point-in-time system resource snapshot."""

    timestamp: float
    cpu_util_pct: float
    mem_util_pct: float
    gpu_util_pct: Optional[float] = None
    gpu_mem_util_pct: Optional[float] = None
    disk_util_pct: Optional[float] = None
    io_wait_pct: Optional[float] = None
    prom_pod_cpu: Optional[float] = None
    prom_pod_memory: Optional[float] = None
    prom_gpu_util: Optional[float] = None
    prom_gpu_memory: Optional[float] = None
    prom_istio_qps: Optional[float] = None


class MetricsMonitor:
    """Collects CPU, memory, and optionally GPU metrics every *interval* seconds.

    Usage::

        monitor = MetricsMonitor(interval_seconds=5)
        task = asyncio.create_task(monitor.start(duration_seconds=60))
        # … run load …
        await task
        snapshots = monitor.snapshots
        flat_metrics = monitor.aggregate()
    """

    def __init__(
        self,
        interval_seconds: float = 5.0,
        prometheus_channel: Any | None = None,
        prometheus_queries: dict[str, str] | None = None,
    ) -> None:
        self._interval = interval_seconds
        self._snapshots: list[SystemSnapshot] = []
        self._stop_event = asyncio.Event()
        self._prometheus_channel = prometheus_channel
        self._prometheus_queries = prometheus_queries or {}

    @property
    def snapshots(self) -> list[SystemSnapshot]:
        return list(self._snapshots)

    async def start(self, duration_seconds: float) -> None:
        """Collect metrics until *duration_seconds* elapses or :meth:`stop` is called."""
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline and not self._stop_event.is_set():
            snapshot = await self._collect()
            self._snapshots.append(snapshot)
            remaining = deadline - time.monotonic()
            await asyncio.sleep(min(self._interval, max(0.0, remaining)))

    def stop(self) -> None:
        """Signal the monitor to stop after the current sleep."""
        self._stop_event.set()

    async def _collect(self) -> SystemSnapshot:
        """Gather a single snapshot from the OS and (optionally) nvidia-smi."""
        cpu = await asyncio.get_event_loop().run_in_executor(None, _cpu_util)
        mem = await asyncio.get_event_loop().run_in_executor(None, _mem_util)
        gpu_util, gpu_mem = await asyncio.get_event_loop().run_in_executor(None, _gpu_util)
        disk = await asyncio.get_event_loop().run_in_executor(None, _disk_util)
        io_wait = await asyncio.get_event_loop().run_in_executor(None, _io_wait)

        prom_data = await self._collect_prometheus()

        return SystemSnapshot(
            timestamp=time.time(),
            cpu_util_pct=cpu,
            mem_util_pct=mem,
            gpu_util_pct=gpu_util,
            gpu_mem_util_pct=gpu_mem,
            disk_util_pct=disk,
            io_wait_pct=io_wait,
            prom_pod_cpu=prom_data.get("pod_cpu"),
            prom_pod_memory=prom_data.get("pod_memory"),
            prom_gpu_util=prom_data.get("gpu_utilization"),
            prom_gpu_memory=prom_data.get("gpu_memory"),
            prom_istio_qps=prom_data.get("istio_qps"),
        )

    async def _collect_prometheus(self) -> dict[str, float]:
        """Collect Prometheus metrics if a channel is configured."""
        if self._prometheus_channel is None or not self._prometheus_queries:
            return {}
        results: dict[str, float] = {}
        for name, promql in self._prometheus_queries.items():
            try:
                value = await self._prometheus_channel.query_instant(promql)
                results[name] = float(value)
            except Exception:
                pass
        return results

    def aggregate(self) -> dict[str, float]:
        """Return mean values across all snapshots, suitable for bottleneck checks."""
        if not self._snapshots:
            return {}

        def mean(values: list[Optional[float]]) -> Optional[float]:
            valid = [v for v in values if v is not None]
            return sum(valid) / len(valid) if valid else None

        result: dict[str, float] = {}
        cpu_mean = mean([s.cpu_util_pct for s in self._snapshots])
        if cpu_mean is not None:
            result["cpu_util_pct"] = round(cpu_mean, 2)

        mem_mean = mean([s.mem_util_pct for s in self._snapshots])
        if mem_mean is not None:
            result["mem_util_pct"] = round(mem_mean, 2)

        gpu_mean = mean([s.gpu_util_pct for s in self._snapshots])
        if gpu_mean is not None:
            result["gpu_util_pct"] = round(gpu_mean, 2)

        gpu_mem_mean = mean([s.gpu_mem_util_pct for s in self._snapshots])
        if gpu_mem_mean is not None:
            result["gpu_mem_util_pct"] = round(gpu_mem_mean, 2)

        disk_mean = mean([s.disk_util_pct for s in self._snapshots])
        if disk_mean is not None:
            result["disk_util_pct"] = round(disk_mean, 2)

        io_mean = mean([s.io_wait_pct for s in self._snapshots])
        if io_mean is not None:
            result["io_wait_pct"] = round(io_mean, 2)

        prom_cpu_mean = mean([s.prom_pod_cpu for s in self._snapshots])
        if prom_cpu_mean is not None:
            result["prom_pod_cpu"] = round(prom_cpu_mean, 4)

        prom_mem_mean = mean([s.prom_pod_memory for s in self._snapshots])
        if prom_mem_mean is not None:
            result["prom_pod_memory"] = round(prom_mem_mean, 2)

        prom_gpu_mean = mean([s.prom_gpu_util for s in self._snapshots])
        if prom_gpu_mean is not None:
            result["prom_gpu_util"] = round(prom_gpu_mean, 2)

        prom_gpu_mem_mean = mean([s.prom_gpu_memory for s in self._snapshots])
        if prom_gpu_mem_mean is not None:
            result["prom_gpu_memory"] = round(prom_gpu_mem_mean, 2)

        prom_qps_mean = mean([s.prom_istio_qps for s in self._snapshots])
        if prom_qps_mean is not None:
            result["prom_istio_qps"] = round(prom_qps_mean, 2)

        return result


# ---------------------------------------------------------------------------
# Probe functions (run in executor threads)
# ---------------------------------------------------------------------------

def _cpu_util() -> float:
    """Return CPU utilization percentage using /proc/stat."""
    try:
        import psutil
        return psutil.cpu_percent(interval=0.2)
    except ImportError:
        pass
    # Fallback: read /proc/stat
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        fields = [float(x) for x in line.strip().split()[1:]]
        idle = fields[3]
        total = sum(fields)
        return max(0.0, min(100.0, (1.0 - idle / total) * 100.0))
    except Exception:
        return 0.0


def _mem_util() -> float:
    """Return system memory utilization percentage."""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        pass
    try:
        mem: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
        total = mem.get("MemTotal", 1)
        available = mem.get("MemAvailable", total)
        used = total - available
        return round(used / total * 100.0, 2)
    except Exception:
        return 0.0


def _gpu_util() -> tuple[Optional[float], Optional[float]]:
    """Return (gpu_util_pct, gpu_mem_util_pct) via nvidia-smi, or (None, None)."""
    cmd = shlex.split(
        "nvidia-smi --query-gpu=utilization.gpu,utilization.memory "
        "--format=csv,noheader,nounits"
    )
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None, None
        first_line = result.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first_line.split(",")]
        gpu_util = float(parts[0])
        mem_util = float(parts[1])
        return gpu_util, mem_util
    except Exception:
        return None, None


def _disk_util() -> Optional[float]:
    """Return disk utilization percentage for the root partition."""
    try:
        import psutil
        usage = psutil.disk_usage("/")
        return round(usage.percent, 2)
    except ImportError:
        pass
    try:
        import os
        stat = os.statvfs("/")
        total = stat.f_blocks
        free = stat.f_bavail
        used = total - free
        return round(used / max(1, total) * 100.0, 2)
    except Exception:
        return None


def _io_wait() -> Optional[float]:
    """Return IO-wait percentage by reading /proc/stat iowait field."""
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        fields = [float(x) for x in line.strip().split()[1:]]
        if len(fields) < 4:
            return None
        total = sum(fields)
        iowait = fields[4] if len(fields) > 4 else 0.0
        return round(iowait / max(1.0, total) * 100.0, 2)
    except Exception:
        return None
