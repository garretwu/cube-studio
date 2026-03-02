"""
VLLM Latency Scenarios - RC-1~RC-6.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fault_injector.config.schema import InjectResult, RecoverResult
from fault_injector.scenarios.base import BaseScenario, FaultContext

logger = logging.getLogger(__name__)


_FAULT_TOKEN_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _safe_fault_token(fault_id: str) -> str:
    token = _FAULT_TOKEN_RE.sub("_", fault_id)
    return token or "fault"


def _pid_file_path(fault_id: str, suffix: str) -> str:
    return f"/tmp/fault_injector_{_safe_fault_token(fault_id)}_{suffix}.pid"


def _python_c_command(code: str) -> str:
    escaped = code.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'python -c "{escaped}"'


def _pid_recover_command(pid_file: str) -> str:
    code = (
        "import os,signal\n"
        f"pid_file = {pid_file!r}\n"
        "if os.path.exists(pid_file):\n"
        "    with open(pid_file, 'r', encoding='utf-8') as f:\n"
        "        raw = f.read().strip()\n"
        "    if raw:\n"
        "        pid = int(raw)\n"
        "        try:\n"
        "            os.kill(pid, signal.SIGTERM)\n"
        "            print('killed')\n"
        "        except ProcessLookupError:\n"
        "            print('already_stopped')\n"
        "    else:\n"
        "        print('already_stopped')\n"
        "    try:\n"
        "        os.remove(pid_file)\n"
        "    except FileNotFoundError:\n"
        "        pass\n"
        "else:\n"
        "    print('pid_missing')\n"
    )
    return _python_c_command(code)


def _mark_recovered_or_failed(ctx: FaultContext, success: bool) -> None:
    if success:
        ctx.rollback.mark_recovered(ctx.fault_id)
    else:
        ctx.rollback.mark_failed(ctx.fault_id)


def _guard_check(ctx: FaultContext, command: str) -> None:
    try:
        ctx.guard.check_command(command, "ssh")
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def _pkill_idempotent(result) -> bool:
    if result.success:
        return True
    err = (result.error or "").lower()
    return "no process found" in err or "not found" in err or not err


class NetworkJitterScenario(BaseScenario):
    @property
    def name(self) -> str:
        return "network_jitter"

    @property
    def description(self) -> str:
        return "Network jitter via tc netem delay injection"

    @property
    def layer(self) -> str:
        return "os"

    def _build_tc_command(self, params: dict[str, Any]) -> str:
        interface = params.get("interface", "eth0")
        delay_ms = params.get("delay_ms", 50)
        jitter_ms = params.get("jitter_ms", 100)
        distribution = params.get("distribution", "pareto")
        loss_pct = params.get("loss_pct", 0)

        cmd = f"tc qdisc replace dev {interface} root netem delay {delay_ms}ms"
        if jitter_ms > 0:
            cmd += f" {jitter_ms}ms"
        if distribution != "normal":
            cmd += f" distribution {distribution}"
        if loss_pct > 0:
            cmd += f" loss {loss_pct}%"
        return cmd

    def _build_recovery_command(self, interface: str) -> str:
        return f"tc qdisc del dev {interface} root"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        interface = ctx.params.get("interface", "eth0")
        delay_ms = ctx.params.get("delay_ms", 50)
        jitter_ms = ctx.params.get("jitter_ms", 100)
        distribution = ctx.params.get("distribution", "pareto")
        loss_pct = ctx.params.get("loss_pct", 0)
        inject_cmd = self._build_tc_command(ctx.params)

        try:
            _guard_check(ctx, inject_cmd)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="tc_add_delay",
            inject_params={
                "node": ctx.target_node,
                "interface": interface,
                "delay_ms": delay_ms,
                "jitter_ms": jitter_ms,
                "distribution": distribution,
                "loss_pct": loss_pct,
            },
            recover_action="tc_del_qdisc",
            recover_params={"node": ctx.target_node, "interface": interface},
        )

        result = await ctx.ssh.run_command(node=ctx.target_node, command=inject_cmd, use_sudo=True)
        if result.success:
            return InjectResult(success=True, fault_id=ctx.fault_id)
        return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        interface = ctx.params.get("interface", "eth0")
        recover_cmd = self._build_recovery_command(interface)
        try:
            _guard_check(ctx, recover_cmd)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        result = await ctx.ssh.run_command(node=ctx.target_node, command=recover_cmd, use_sudo=True)
        success = bool(result.success or "No such file or directory" in result.error or "Cannot delete" in result.error)
        _mark_recovered_or_failed(ctx, success)
        if success:
            return RecoverResult(success=True, fault_id=ctx.fault_id)
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def verify(self, ctx: FaultContext) -> bool:
        interface = ctx.params.get("interface", "eth0")
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"tc qdisc show dev {interface}",
            use_sudo=True,
        )
        if not result.success:
            return True
        return "netem" not in result.output

    def monitor_queries(self) -> dict[str, str]:
        return {
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p95": 'histogram_quantile(0.95, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "network_latency": 'histogram_quantile(0.95, rate(network_latency_seconds_bucket[1m]))',
        }


class GPUContentionScenario(BaseScenario):
    @property
    def name(self) -> str:
        return "gpu_contention"

    @property
    def description(self) -> str:
        return "GPU resource contention via gpu-burn saturation"

    @property
    def layer(self) -> str:
        return "hardware"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = int(ctx.params.get("duration", 300))
        gpu_id = int(ctx.params.get("gpu_id", 0))
        intensity = int(ctx.params.get("intensity", 100))
        marker = f"fi_gpu_burn_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "gpu_burn")
        inject_code = (
            "import os,subprocess\n"
            f"marker = {marker!r}\n"
            f"log_file = '/tmp/{marker}.log'\n"
            f"pid_file = {pid_file!r}\n"
            "env = dict(os.environ)\n"
            f"env['CUDA_VISIBLE_DEVICES'] = {str(gpu_id)!r}\n"
            "with open(log_file, 'w', encoding='utf-8') as log:\n"
            "    proc = subprocess.Popen(\n"
            f"        [marker, {str(duration)!r}],\n"
            "        executable='/tmp/gpu-burn/gpu_burn',\n"
            "        cwd='/tmp/gpu-burn',\n"
            "        stdout=log,\n"
            "        stderr=subprocess.STDOUT,\n"
            "        env=env,\n"
            "    )\n"
            "with open(pid_file, 'w', encoding='utf-8') as f:\n"
            "    f.write(str(proc.pid))\n"
            "print(proc.pid)\n"
        )
        inject_cmd = _python_c_command(inject_code)

        try:
            _guard_check(ctx, inject_cmd)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="gpu_burn",
            inject_params={
                "duration": duration,
                "gpu_id": gpu_id,
                "intensity": intensity,
                "marker": marker,
                "pid_file": pid_file,
            },
            recover_action="kill_gpu_burn",
            recover_params={"marker": marker, "pid_file": pid_file},
        )

        result = await ctx.ssh.run_command(node=ctx.target_node, command=inject_cmd, use_sudo=True)
        if result.success:
            return InjectResult(success=True, fault_id=ctx.fault_id)
        return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        marker = f"fi_gpu_burn_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "gpu_burn")
        pid_cmd = _pid_recover_command(pid_file)
        pkill_cmd = f"pkill -f '{marker}'"
        try:
            _guard_check(ctx, pid_cmd)
            _guard_check(ctx, pkill_cmd)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        pid_result = await ctx.ssh.run_command(node=ctx.target_node, command=pid_cmd, use_sudo=True)
        if not pid_result.success:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=pid_result.error)

        pkill_result = await ctx.ssh.run_command(node=ctx.target_node, command=pkill_cmd, use_sudo=True)
        success = _pkill_idempotent(pkill_result)
        _mark_recovered_or_failed(ctx, success)
        if success:
            return RecoverResult(success=True, fault_id=ctx.fault_id)
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=pkill_result.error)

    async def verify(self, ctx: FaultContext) -> bool:
        marker = f"fi_gpu_burn_{_safe_fault_token(ctx.fault_id)}"
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"pgrep -f '{marker}' || echo 'not_running'",
            use_sudo=True,
        )
        return "not_running" in result.output

    def monitor_queries(self) -> dict[str, str]:
        return {
            "gpu_util": 'DCGM_FI_DEV_GPU_UTIL{{node="{node}"}}',
            "gpu_mem_used": 'DCGM_FI_DEV_FB_USED{{node="{node}"}}',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "ttft": 'histogram_quantile(0.95, rate(vllm:time_to_first_token_seconds_bucket[1m]))',
        }


class StorageIOInterferenceScenario(BaseScenario):
    @property
    def name(self) -> str:
        return "storage_io_interference"

    @property
    def description(self) -> str:
        return "Storage I/O interference via fio stress workload"

    @property
    def layer(self) -> str:
        return "os"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = int(ctx.params.get("duration", 300))
        filename = str(ctx.params.get("filename", "/data/testfile"))
        rw_mode = str(ctx.params.get("rw_mode", "randwrite"))
        bs = str(ctx.params.get("bs", "4k"))
        iodepth = int(ctx.params.get("iodepth", 128))
        numjobs = int(ctx.params.get("numjobs", 8))
        marker = f"fi_fio_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "fio")
        inject_code = (
            "import subprocess\n"
            f"marker = {marker!r}\n"
            f"log_file = '/tmp/{marker}.log'\n"
            f"pid_file = {pid_file!r}\n"
            "args = [\n"
            "    marker,\n"
            f"    '--name={marker}',\n"
            f"    '--filename={filename}',\n"
            f"    '--rw={rw_mode}',\n"
            f"    '--bs={bs}',\n"
            f"    '--iodepth={iodepth}',\n"
            f"    '--numjobs={numjobs}',\n"
            "    '--size=10G',\n"
            "    '--time_based',\n"
            f"    '--runtime={duration}',\n"
            "]\n"
            "with open(log_file, 'w', encoding='utf-8') as log:\n"
            "    proc = subprocess.Popen(\n"
            "        args,\n"
            "        executable='fio',\n"
            "        stdout=log,\n"
            "        stderr=subprocess.STDOUT,\n"
            "    )\n"
            "with open(pid_file, 'w', encoding='utf-8') as f:\n"
            "    f.write(str(proc.pid))\n"
            "print(proc.pid)\n"
        )
        inject_cmd = _python_c_command(inject_code)

        try:
            _guard_check(ctx, inject_cmd)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="fio_stress",
            inject_params={
                "duration": duration,
                "filename": filename,
                "rw_mode": rw_mode,
                "bs": bs,
                "iodepth": iodepth,
                "numjobs": numjobs,
                "marker": marker,
                "pid_file": pid_file,
            },
            recover_action="kill_fio",
            recover_params={"marker": marker, "pid_file": pid_file},
        )

        result = await ctx.ssh.run_command(node=ctx.target_node, command=inject_cmd, use_sudo=True)
        if result.success:
            return InjectResult(success=True, fault_id=ctx.fault_id)
        return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        marker = f"fi_fio_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "fio")
        pid_cmd = _pid_recover_command(pid_file)
        pkill_cmd = f"pkill -f '{marker}'"
        try:
            _guard_check(ctx, pid_cmd)
            _guard_check(ctx, pkill_cmd)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        pid_result = await ctx.ssh.run_command(node=ctx.target_node, command=pid_cmd, use_sudo=True)
        if not pid_result.success:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=pid_result.error)

        pkill_result = await ctx.ssh.run_command(node=ctx.target_node, command=pkill_cmd, use_sudo=True)
        success = _pkill_idempotent(pkill_result)
        _mark_recovered_or_failed(ctx, success)
        if success:
            return RecoverResult(success=True, fault_id=ctx.fault_id)
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=pkill_result.error)

    async def verify(self, ctx: FaultContext) -> bool:
        marker = f"fi_fio_{_safe_fault_token(ctx.fault_id)}"
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"pgrep -f '{marker}' || echo 'not_running'",
            use_sudo=True,
        )
        return "not_running" in result.output

    def monitor_queries(self) -> dict[str, str]:
        return {
            "disk_io_util": 'node_disk_io_utilization_seconds{{device="{device}"}}',
            "disk_io_wait": 'node_disk_io_time_weighted_seconds{{device="{device}"}}',
            "disk_read_bytes": 'node_disk_read_bytes_total{{device="{device}"}}',
            "disk_write_bytes": 'node_disk_written_bytes_total{{device="{device}"}}',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
        }


class PlatformCascadeScenario(BaseScenario):
    @property
    def name(self) -> str:
        return "platform_cascade"

    @property
    def description(self) -> str:
        return "Platform cascade latency via MySQL/Redis delay injection"

    @property
    def layer(self) -> str:
        return "platform"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        target_component = ctx.params.get("target_component", "mysql")
        delay_ms = int(ctx.params.get("delay_ms", 100))
        port = int(ctx.params.get("port", 3306))
        interface = str(ctx.params.get("interface", "eth0"))

        inject_cmd = f"tc qdisc replace dev {interface} root netem delay {delay_ms}ms"
        try:
            _guard_check(ctx, inject_cmd)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="platform_delay",
            inject_params={
                "target_component": target_component,
                "delay_ms": delay_ms,
                "port": port,
                "interface": interface,
            },
            recover_action="tc_del_qdisc",
            recover_params={"interface": interface},
        )

        result = await ctx.ssh.run_command(node=ctx.target_node, command=inject_cmd, use_sudo=True)
        if result.success:
            return InjectResult(success=True, fault_id=ctx.fault_id)
        return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        interface = str(ctx.params.get("interface", "eth0"))
        recover_cmd = f"tc qdisc del dev {interface} root"
        try:
            _guard_check(ctx, recover_cmd)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        result = await ctx.ssh.run_command(node=ctx.target_node, command=recover_cmd, use_sudo=True)
        success = bool(result.success or "No such file or directory" in result.error or "Cannot delete" in result.error)
        _mark_recovered_or_failed(ctx, success)
        if success:
            return RecoverResult(success=True, fault_id=ctx.fault_id)
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def verify(self, ctx: FaultContext) -> bool:
        interface = str(ctx.params.get("interface", "eth0"))
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"tc qdisc show dev {interface} | grep -q netem && echo 'exists' || echo 'not_exists'",
            use_sudo=True,
        )
        return "not_exists" in result.output

    def monitor_queries(self) -> dict[str, str]:
        return {
            "mysql_latency": "mysql_query_duration_seconds",
            "redis_latency": "redis_command_duration_seconds",
            "k8s_api_latency": "kubernetes_api_request_duration_seconds",
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "request_queue_depth": "vllm:request_queue_depth",
        }


class OSResourcePressureScenario(BaseScenario):
    @property
    def name(self) -> str:
        return "os_resource_pressure"

    @property
    def description(self) -> str:
        return "OS resource pressure via stress-ng CPU and memory load"

    @property
    def layer(self) -> str:
        return "os"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = int(ctx.params.get("duration", 300))
        vm_bytes_percent = int(ctx.params.get("vm_bytes_percent", 80))
        cpu_workers = int(ctx.params.get("cpu_workers", 64))
        cpu_load = int(ctx.params.get("cpu_load", 90))
        io_workers = int(ctx.params.get("io_workers", 4))
        marker = f"fi_stress_ng_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "stress_ng")
        inject_code = (
            "import subprocess\n"
            f"marker = {marker!r}\n"
            f"log_file = '/tmp/{marker}.log'\n"
            f"pid_file = {pid_file!r}\n"
            "args = [\n"
            "    marker,\n"
            "    '--vm', '4',\n"
            f"    '--vm-bytes', '{vm_bytes_percent}%',\n"
            f"    '--cpu', '{cpu_workers}',\n"
            f"    '--cpu-load', '{cpu_load}',\n"
            f"    '--io', '{io_workers}',\n"
            f"    '--timeout', '{duration}s',\n"
            "]\n"
            "with open(log_file, 'w', encoding='utf-8') as log:\n"
            "    proc = subprocess.Popen(\n"
            "        args,\n"
            "        executable='stress-ng',\n"
            "        stdout=log,\n"
            "        stderr=subprocess.STDOUT,\n"
            "    )\n"
            "with open(pid_file, 'w', encoding='utf-8') as f:\n"
            "    f.write(str(proc.pid))\n"
            "print(proc.pid)\n"
        )
        inject_cmd = _python_c_command(inject_code)

        try:
            _guard_check(ctx, inject_cmd)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="stress_ng",
            inject_params={
                "duration": duration,
                "vm_bytes_percent": vm_bytes_percent,
                "cpu_workers": cpu_workers,
                "cpu_load": cpu_load,
                "io_workers": io_workers,
                "marker": marker,
                "pid_file": pid_file,
            },
            recover_action="kill_stress_ng",
            recover_params={"marker": marker, "pid_file": pid_file},
        )

        result = await ctx.ssh.run_command(node=ctx.target_node, command=inject_cmd, use_sudo=True)
        if result.success:
            return InjectResult(success=True, fault_id=ctx.fault_id)
        return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        marker = f"fi_stress_ng_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "stress_ng")
        pid_cmd = _pid_recover_command(pid_file)
        pkill_cmd = f"pkill -f '{marker}'"
        try:
            _guard_check(ctx, pid_cmd)
            _guard_check(ctx, pkill_cmd)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        pid_result = await ctx.ssh.run_command(node=ctx.target_node, command=pid_cmd, use_sudo=True)
        if not pid_result.success:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=pid_result.error)

        pkill_result = await ctx.ssh.run_command(node=ctx.target_node, command=pkill_cmd, use_sudo=True)
        success = _pkill_idempotent(pkill_result)
        _mark_recovered_or_failed(ctx, success)
        if success:
            return RecoverResult(success=True, fault_id=ctx.fault_id)
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=pkill_result.error)

    async def verify(self, ctx: FaultContext) -> bool:
        marker = f"fi_stress_ng_{_safe_fault_token(ctx.fault_id)}"
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"pgrep -f '{marker}' || echo 'not_running'",
            use_sudo=True,
        )
        return "not_running" in result.output

    def monitor_queries(self) -> dict[str, str]:
        return {
            "cpu_util": '1 - rate(node_cpu_seconds_total{{mode="idle"}}[1m])',
            "memory_util": '1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
            "io_util": "rate(node_disk_io_time_seconds_total[1m])",
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "oom_events": "increase(node_oom_events_total[5m])",
        }


class ThermalThrottlingScenario(BaseScenario):
    @property
    def name(self) -> str:
        return "thermal_throttling"

    @property
    def description(self) -> str:
        return "Thermal throttling simulation via GPU power limit"

    @property
    def layer(self) -> str:
        return "hardware"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        gpu_id = int(ctx.params.get("gpu_id", 0))
        power_limit = int(ctx.params.get("power_limit", 150))
        get_power_cmd = f"nvidia-smi -i {gpu_id} --query-gpu=power.limit --format=csv,noheader,nounits"
        get_result = await ctx.ssh.run_command(node=ctx.target_node, command=get_power_cmd, use_sudo=False)

        original_power = 300
        if get_result.success:
            try:
                original_power = int(float(get_result.output.strip()))
            except ValueError:
                logger.warning("Unable to parse original power limit: %s", get_result.output)
        ctx.params["original_power"] = original_power

        inject_cmd = f"nvidia-smi -i {gpu_id} -pl {power_limit}"
        try:
            _guard_check(ctx, inject_cmd)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="gpu_power_limit",
            inject_params={"gpu_id": gpu_id, "power_limit": power_limit},
            recover_action="gpu_power_restore",
            recover_params={"gpu_id": gpu_id, "original_power": original_power},
        )

        result = await ctx.ssh.run_command(node=ctx.target_node, command=inject_cmd, use_sudo=True)
        if result.success:
            return InjectResult(success=True, fault_id=ctx.fault_id)
        return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        gpu_id = int(ctx.params.get("gpu_id", 0))
        original_power = int(ctx.params.get("original_power", 300))
        recover_cmd = f"nvidia-smi -i {gpu_id} -pl {original_power}"
        try:
            _guard_check(ctx, recover_cmd)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        result = await ctx.ssh.run_command(node=ctx.target_node, command=recover_cmd, use_sudo=True)
        success = bool(result.success or result.dry_run)
        _mark_recovered_or_failed(ctx, success)
        if success:
            return RecoverResult(success=True, fault_id=ctx.fault_id)
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def verify(self, ctx: FaultContext) -> bool:
        gpu_id = int(ctx.params.get("gpu_id", 0))
        original_power = int(ctx.params.get("original_power", 300))
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"nvidia-smi -i {gpu_id} --query-gpu=power.limit --format=csv,noheader,nounits",
            use_sudo=False,
        )
        if not result.success:
            return True
        try:
            current_power = int(float(result.output.strip()))
            return current_power >= original_power - 10
        except ValueError:
            return True

    def monitor_queries(self) -> dict[str, str]:
        return {
            "gpu_temp": 'DCGM_FI_DEV_GPU_TEMP{{node="{node}"}}',
            "gpu_power": 'DCGM_FI_DEV_POWER_USAGE{{node="{node}"}}',
            "gpu_sm_clock": 'DCGM_FI_DEV_SM_CLOCK{{node="{node}"}}',
            "gpu_throttle_reason": 'DCGM_FI_DEV_GPU_UTIL{{node="{node}"}}',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_throughput": "rate(vllm:request_duration_seconds_count[1m])",
        }


SCENARIOS = [
    GPUContentionScenario,
    NetworkJitterScenario,
    StorageIOInterferenceScenario,
    PlatformCascadeScenario,
    OSResourcePressureScenario,
    ThermalThrottlingScenario,
]
