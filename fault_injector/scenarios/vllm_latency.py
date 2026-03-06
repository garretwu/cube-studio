"""
VLLM Latency Scenarios - RC-1~RC-6.
"""
from __future__ import annotations

import json
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


def _python_c_command(code: str, interpreter: str) -> str:
    escaped = code.replace("\\", "\\\\").replace('"', '\\"')
    return f'{interpreter} -c "{escaped}"'


def _pid_recover_command(pid_file: str, interpreter: str) -> str:
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
    return _python_c_command(code, interpreter=interpreter)


def _gpu_burn_pid_recover_command(pid_file: str, interpreter: str) -> str:
    code = (
        "import json,os,signal\n"
        f"pid_file = {pid_file!r}\n"
        "pids = []\n"
        "if os.path.exists(pid_file):\n"
        "    with open(pid_file, 'r', encoding='utf-8') as f:\n"
        "        raw = f.read().strip()\n"
        "    if raw:\n"
        "        parsed = None\n"
        "        try:\n"
        "            parsed = json.loads(raw)\n"
        "        except Exception:\n"
        "            parsed = None\n"
        "        if isinstance(parsed, dict):\n"
        "            for value in parsed.values():\n"
        "                try:\n"
        "                    pids.append(int(value))\n"
        "                except Exception:\n"
        "                    pass\n"
        "        elif isinstance(parsed, list):\n"
        "            for value in parsed:\n"
        "                try:\n"
        "                    pids.append(int(value))\n"
        "                except Exception:\n"
        "                    pass\n"
        "        else:\n"
        "            try:\n"
        "                pids.append(int(raw))\n"
        "            except Exception:\n"
        "                pass\n"
        "    for pid in pids:\n"
        "        try:\n"
        "            os.kill(pid, signal.SIGTERM)\n"
        "        except ProcessLookupError:\n"
        "            pass\n"
        "    try:\n"
        "        os.remove(pid_file)\n"
        "    except FileNotFoundError:\n"
        "        pass\n"
        "print('killed=' + str(len(pids)))\n"
    )
    return _python_c_command(code, interpreter=interpreter)


def _parse_gpu_ids(params: dict[str, Any]) -> list[int]:
    raw_gpu_ids = params.get("gpu_ids")
    if raw_gpu_ids is None:
        # Backward compatibility: legacy single-gpu field.
        legacy_gpu_id = params.get("gpu_id")
        if legacy_gpu_id is None or str(legacy_gpu_id).strip() == "":
            return []
        return [int(legacy_gpu_id)]

    if isinstance(raw_gpu_ids, list):
        parsed: list[int] = []
        for value in raw_gpu_ids:
            parsed.append(int(value))
        return list(dict.fromkeys(parsed))

    if isinstance(raw_gpu_ids, str):
        text = raw_gpu_ids.strip()
        if not text:
            return []
        parsed = [int(part.strip()) for part in text.split(",") if part.strip()]
        return list(dict.fromkeys(parsed))

    return [int(raw_gpu_ids)]


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


def _result_error(result) -> str:
    return (result.error or result.output or "").strip()


def _is_endpoint_not_supported(message: str) -> bool:
    text = (message or "").lower()
    return "404" in text or "not found" in text or "http status error '404" in text


def _is_unauthorized_error(message: str) -> bool:
    text = (message or "").lower()
    return "401" in text or "unauthorized" in text


def _parse_json_payload(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {}


def _fan_snapshot(thermal_payload: dict[str, Any]) -> dict[str, Any]:
    fans = thermal_payload.get("Fans", [])
    if not isinstance(fans, list):
        fans = []
    sample: list[dict[str, Any]] = []
    for fan in fans[:5]:
        if not isinstance(fan, dict):
            continue
        sample.append(
            {
                "name": fan.get("Name"),
                "reading": fan.get("Reading"),
                "reading_units": fan.get("ReadingUnits"),
                "status": fan.get("Status"),
            }
        )
    return {"fan_count": len(fans), "fans_sample": sample}


def _temperature_snapshot(thermal_payload: dict[str, Any]) -> dict[str, Any]:
    temps = thermal_payload.get("Temperatures", [])
    if not isinstance(temps, list):
        temps = []
    sample: list[dict[str, Any]] = []
    for temp in temps[:5]:
        if not isinstance(temp, dict):
            continue
        sample.append(
            {
                "name": temp.get("Name"),
                "reading_celsius": temp.get("ReadingCelsius"),
                "status": temp.get("Status"),
            }
        )
    return {"temperature_count": len(temps), "temperatures_sample": sample}


def _extract_sensor_list(payload: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    sensors = payload.get("sensors", [])
    if not isinstance(sensors, list):
        return []
    out: list[dict[str, Any]] = []
    for item in sensors[:limit]:
        if isinstance(item, dict):
            out.append(item)
    return out


async def _ipmi_collect_snapshot(ctx: FaultContext, ipmi_cfg: Any) -> dict[str, Any]:
    if not ctx.ipmi:
        return {
            "host": ipmi_cfg.host,
            "interface": ipmi_cfg.interface,
            "mc_info_ok": False,
            "fan_query_ok": False,
            "temp_query_ok": False,
            "mc_info_excerpt": "",
            "fan_sensors": [],
            "temperature_sensors": [],
            "errors": ["IPMI channel not initialized"],
        }

    timeout = int(getattr(ipmi_cfg, "timeout", 30) or 30)
    host = str(ipmi_cfg.host)
    username = str(ipmi_cfg.username or "")
    password = str(ipmi_cfg.password or "")
    port = int(getattr(ipmi_cfg, "port", 623) or 623)
    interface = str(getattr(ipmi_cfg, "interface", "lanplus") or "lanplus")

    mc_info = await ctx.ipmi.get_mc_info(
        host=host,
        username=username,
        password=password,
        port=port,
        interface=interface,
        timeout=timeout,
    )
    fan_data = await ctx.ipmi.get_sensor_data(
        host=host,
        username=username,
        password=password,
        sensor_type="fan",
        port=port,
        interface=interface,
        timeout=timeout,
    )
    temp_data = await ctx.ipmi.get_sensor_data(
        host=host,
        username=username,
        password=password,
        sensor_type="temperature",
        port=port,
        interface=interface,
        timeout=timeout,
    )

    mc_payload = _parse_json_payload(mc_info.output)
    fan_payload = _parse_json_payload(fan_data.output)
    temp_payload = _parse_json_payload(temp_data.output)
    return {
        "host": host,
        "interface": interface,
        "mc_info_ok": bool(mc_info.success),
        "fan_query_ok": bool(fan_data.success),
        "temp_query_ok": bool(temp_data.success),
        "mc_info_excerpt": json.dumps(mc_payload, ensure_ascii=False)[:400] if mc_payload else "",
        "fan_sensors": _extract_sensor_list(fan_payload),
        "temperature_sensors": _extract_sensor_list(temp_payload),
        "errors": [e for e in [mc_info.error, fan_data.error, temp_data.error] if e],
    }


def _build_ipmi_profile_commands(profile: str, target_pwm: int) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    if profile == "supermicro_raw":
        percent = max(0, min(100, target_pwm))
        pwm_byte = int(round((percent / 100.0) * 255))
        set_manual_and_pwm = [
            {"netfn": 0x30, "command": 0x30, "data": [0x01, 0x00]},
            {"netfn": 0x30, "command": 0x30, "data": [0x02, 0xFF, pwm_byte]},
        ]
        set_auto = {"netfn": 0x30, "command": 0x30, "data": [0x01, 0x01]}
        return set_manual_and_pwm, set_auto
    return None, None


async def _resolve_python_interpreter(ctx: FaultContext) -> str:
    cached = ctx.params.get("_python_exec")
    if isinstance(cached, str) and cached:
        return cached

    candidates = ["/usr/bin/python3", "python3", "python"]
    failures: list[str] = []
    for candidate in candidates:
        probe_cmd = f"{candidate} -V"
        try:
            _guard_check(ctx, probe_cmd)
        except Exception as exc:
            failures.append(f"{candidate}: {exc}")
            continue

        probe = await ctx.ssh.run_command(node=ctx.target_node, command=probe_cmd, use_sudo=True)
        if probe.success:
            ctx.params["_python_exec"] = candidate
            return candidate
        failures.append(f"{candidate}: {_result_error(probe)}")

    raise RuntimeError("No usable python interpreter found for sudo context: " + " | ".join(failures))


def _platform_error_details(
    *,
    target_component: str,
    interface: str,
    port: int,
    delay_ms: int,
    message: str,
) -> str:
    return (
        f"{message} "
        f"(target_component={target_component}, interface={interface}, port={port}, delay_ms={delay_ms})"
    )


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
        ls_error = self._start_load_simulator_task(ctx)
        if ls_error:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=ls_error)

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
        await self._cancel_load_simulator_task(ctx)
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
            "network_latency": 'histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket[1m])) by (le))',
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
        gpu_ids = _parse_gpu_ids(ctx.params)
        memory = str(ctx.params.get("memory", "60%")).strip() or "60%"
        intensity = int(ctx.params.get("intensity", 100))
        marker = f"fi_gpu_burn_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "gpu_burn")
        try:
            python_exec = await _resolve_python_interpreter(ctx)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))
        inject_code = (
            "import json,subprocess\n"
            f"marker = {marker!r}\n"
            f"gpu_ids = {gpu_ids!r}\n"
            f"memory = {memory!r}\n"
            f"duration = {duration!r}\n"
            f"pid_file = {pid_file!r}\n"
            "targets = gpu_ids if gpu_ids else [None]\n"
            "pid_map = {}\n"
            "for gpu in targets:\n"
            "    args = [marker, '-m', memory]\n"
            "    if gpu is not None:\n"
            "        args.extend(['-i', str(gpu)])\n"
            "    args.append(str(duration))\n"
            "    gpu_suffix = 'all' if gpu is None else f'gpu{gpu}'\n"
            "    log_file = f'/tmp/{marker}.{gpu_suffix}.log'\n"
            "    with open(log_file, 'w', encoding='utf-8') as log:\n"
            "        proc = subprocess.Popen(\n"
            "            args,\n"
            "            executable='/tmp/gpu-burn/gpu_burn',\n"
            "            cwd='/tmp/gpu-burn',\n"
            "            stdout=log,\n"
            "            stderr=subprocess.STDOUT,\n"
            "        )\n"
            "    pid_map[gpu_suffix] = proc.pid\n"
            "with open(pid_file, 'w', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(pid_map))\n"
            "print(json.dumps(pid_map))\n"
        )
        inject_cmd = _python_c_command(inject_code, interpreter=python_exec)

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
                "gpu_ids": gpu_ids,
                "memory": memory,
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
        return InjectResult(success=False, fault_id=ctx.fault_id, error=_result_error(result))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        marker = f"fi_gpu_burn_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "gpu_burn")
        try:
            python_exec = await _resolve_python_interpreter(ctx)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))
        pid_cmd = _gpu_burn_pid_recover_command(pid_file, interpreter=python_exec)
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
        try:
            python_exec = await _resolve_python_interpreter(ctx)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))
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
        inject_cmd = _python_c_command(inject_code, interpreter=python_exec)

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
        return InjectResult(success=False, fault_id=ctx.fault_id, error=_result_error(result))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        marker = f"fi_fio_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "fio")
        try:
            python_exec = await _resolve_python_interpreter(ctx)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))
        pid_cmd = _pid_recover_command(pid_file, interpreter=python_exec)
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
        
        if "not_running" in result.output:
            logger.info("验证通过: gpu-burn 已停止")
            return True

        # 检查返回的 PID 是否真的存在
        pid = result.output.strip()
        if pid:
            check_result = await ctx.ssh.run_command(
                node=ctx.target_node,
                command=f"ps -p {pid} -o pid= || echo 'not_exists'",
                use_sudo=False,
            )
            if "not_exists" in check_result.output:
                logger.info(f"验证通过: PID {pid} 已不存在")
                return True

        logger.warning(f"验证失败: gpu-burn 仍在运行, PID={pid}")
        return False


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
        ls_error = self._start_load_simulator_task(ctx)
        if ls_error:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=ls_error)

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
        await self._cancel_load_simulator_task(ctx)
        details = _platform_error_details(
            target_component=str(target_component),
            interface=interface,
            port=port,
            delay_ms=delay_ms,
            message=_result_error(result),
        )
        return InjectResult(success=False, fault_id=ctx.fault_id, error=details)

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
        details = _platform_error_details(
            target_component=str(ctx.params.get("target_component", "mysql")),
            interface=interface,
            port=int(ctx.params.get("port", 3306)),
            delay_ms=int(ctx.params.get("delay_ms", 100)),
            message=_result_error(result),
        )
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=details)

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
        try:
            python_exec = await _resolve_python_interpreter(ctx)
        except Exception as exc:
            return InjectResult(success=False, fault_id=ctx.fault_id, error=str(exc))
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
        inject_cmd = _python_c_command(inject_code, interpreter=python_exec)

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
        return InjectResult(success=False, fault_id=ctx.fault_id, error=_result_error(result))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        marker = f"fi_stress_ng_{_safe_fault_token(ctx.fault_id)}"
        pid_file = _pid_file_path(ctx.fault_id, "stress_ng")
        try:
            python_exec = await _resolve_python_interpreter(ctx)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))
        pid_cmd = _pid_recover_command(pid_file, interpreter=python_exec)
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
        fan_backend = str(ctx.params.get("fan_control_backend", "auto")).strip().lower()
        ipmi_profile = str(ctx.params.get("ipmi_profile", "read_only")).strip().lower()
        ipmi_target_pwm = int(ctx.params.get("ipmi_target_pwm", ctx.params.get("fan_pwm", 30)))
        if not ctx.redfish:
            return InjectResult(success=False, fault_id=ctx.fault_id, error="Redfish channel not initialized")
        if not ctx.target_redfish:
            return InjectResult(success=False, fault_id=ctx.fault_id, error="Missing target Redfish config for node")

        bmc_cfg = ctx.target_redfish
        bmc_host = bmc_cfg.bmc_host
        verify_tls = bool(bmc_cfg.verify_tls)
        fan_index = int(ctx.params.get("fan_index", 0))
        fan_mode = str(ctx.params.get("fan_mode", "Manual"))
        fan_pwm_raw = ctx.params.get("fan_pwm", 30)
        fan_pwm = int(fan_pwm_raw) if fan_pwm_raw is not None else None
        fan_inject_applied = False
        fan_inject_warning = ""
        fan_injected_backend = ""
        created_session = False
        try:
            if bmc_cfg.token:
                ctx.redfish.set_token(bmc_host, bmc_cfg.token)
            else:
                if not bmc_cfg.username or not bmc_cfg.password:
                    return InjectResult(
                        success=False,
                        fault_id=ctx.fault_id,
                        error="Missing Redfish credentials (username/password)",
                    )
                auth = await ctx.redfish.authenticate(
                    bmc_host=bmc_host,
                    username=bmc_cfg.username,
                    password=bmc_cfg.password,
                    verify_tls=verify_tls,
                )
                if not auth.success:
                    return InjectResult(success=False, fault_id=ctx.fault_id, error=f"Redfish auth failed: {auth.error}")
                created_session = True

            thermal = await ctx.redfish.get_thermal(bmc_host=bmc_host, verify_tls=verify_tls)
            if not thermal.success:
                return InjectResult(
                    success=False,
                    fault_id=ctx.fault_id,
                    error=f"Redfish thermal precheck failed: {_result_error(thermal)}",
                )

            power = await ctx.redfish.get_power(bmc_host=bmc_host, verify_tls=verify_tls)
            if not power.success:
                return InjectResult(
                    success=False,
                    fault_id=ctx.fault_id,
                    error=f"Redfish power precheck failed: {_result_error(power)}",
                )

            sensors = await ctx.redfish.get_sensors(bmc_host=bmc_host, verify_tls=verify_tls)
            thermal_payload = _parse_json_payload(thermal.output)
            power_payload = _parse_json_payload(power.output)
            sensors_payload = _parse_json_payload(sensors.output) if sensors.success else {}

            fan_before = _fan_snapshot(thermal_payload)
            temp_before = _temperature_snapshot(thermal_payload)
            power_controls = (
                power_payload.get("PowerControl", [])
                if isinstance(power_payload.get("PowerControl", []), list)
                else []
            )
            sensor_members = (
                sensors_payload.get("Members", [])
                if isinstance(sensors_payload.get("Members", []), list)
                else []
            )

            if fan_backend != "ipmi":
                set_fan = await ctx.redfish.set_fan_control(
                    bmc_host=bmc_host,
                    fan_index=fan_index,
                    mode=fan_mode,
                    pwm=fan_pwm,
                    verify_tls=verify_tls,
                    fault_id=ctx.fault_id,
                )
                if not set_fan.success:
                    set_fan_error = _result_error(set_fan)
                    if _is_endpoint_not_supported(set_fan_error):
                        fan_inject_warning = set_fan_error
                        logger.warning("Redfish fan inject endpoint unsupported on %s: %s", bmc_host, set_fan_error)
                    else:
                        return InjectResult(
                            success=False,
                            fault_id=ctx.fault_id,
                            error=f"Redfish fan inject failed: {set_fan_error}",
                        )
                else:
                    fan_inject_applied = True
                    fan_injected_backend = "redfish"

            should_try_ipmi = fan_backend == "ipmi" or (fan_backend == "auto" and not fan_inject_applied)
            if should_try_ipmi:
                strict_ipmi = fan_backend == "ipmi"
                if not ctx.target_ipmi:
                    if strict_ipmi:
                        return InjectResult(success=False, fault_id=ctx.fault_id, error="Missing target IPMI config for node")
                    fan_inject_warning = f"{fan_inject_warning}; Missing target IPMI config for node".strip("; ")
                else:
                    ipmi_cfg = ctx.target_ipmi
                    ipmi_pre = await _ipmi_collect_snapshot(ctx, ipmi_cfg)
                    ctx.params["ipmi_precheck"] = ipmi_pre
                    manual_cmds, auto_cmd = _build_ipmi_profile_commands(ipmi_profile, ipmi_target_pwm)
                    if ipmi_profile == "read_only":
                        if strict_ipmi:
                            return InjectResult(
                                success=False,
                                fault_id=ctx.fault_id,
                                error="IPMI backend requested but ipmi_profile is read_only",
                            )
                        fan_inject_warning = f"{fan_inject_warning}; IPMI profile read_only, skipped injection".strip("; ")
                    elif manual_cmds is None or auto_cmd is None:
                        if strict_ipmi:
                            return InjectResult(
                                success=False,
                                fault_id=ctx.fault_id,
                                error=f"Unsupported IPMI profile: {ipmi_profile}",
                            )
                        fan_inject_warning = f"{fan_inject_warning}; Unsupported IPMI profile: {ipmi_profile}".strip("; ")
                    else:
                        command_results: list[dict[str, Any]] = []
                        ipmi_ok = True
                        ipmi_error = ""
                        if not ctx.ipmi:
                            ipmi_ok = False
                            ipmi_error = "IPMI channel not initialized"
                        for raw_cmd in manual_cmds:
                            if not ctx.ipmi:
                                break
                            raw_result = await ctx.ipmi.raw_command(
                                host=str(ipmi_cfg.host),
                                username=str(ipmi_cfg.username or ""),
                                password=str(ipmi_cfg.password or ""),
                                netfn=int(raw_cmd["netfn"]),
                                command=int(raw_cmd["command"]),
                                data=[int(x) for x in raw_cmd.get("data", [])],
                                port=int(getattr(ipmi_cfg, "port", 623) or 623),
                                interface=str(getattr(ipmi_cfg, "interface", "lanplus") or "lanplus"),
                                timeout=int(getattr(ipmi_cfg, "timeout", 30) or 30),
                            )
                            command_results.append(
                                {
                                    "command": raw_cmd,
                                    "success": bool(raw_result.success),
                                    "output": raw_result.output,
                                    "error": raw_result.error,
                                }
                            )
                            if not raw_result.success:
                                ipmi_ok = False
                                ipmi_error = _result_error(raw_result)
                                break
                        ipmi_post = await _ipmi_collect_snapshot(ctx, ipmi_cfg)
                        ctx.params["ipmi_inject"] = {
                            "profile": ipmi_profile,
                            "target_pwm": ipmi_target_pwm,
                            "before": ipmi_pre,
                            "after": ipmi_post,
                            "commands": command_results,
                            "recover_command": auto_cmd,
                            "applied": ipmi_ok,
                            "error": ipmi_error,
                        }
                        if not ipmi_ok:
                            if strict_ipmi:
                                return InjectResult(
                                    success=False,
                                    fault_id=ctx.fault_id,
                                    error=f"IPMI fan inject command failed: {ipmi_error}",
                                )
                            fan_inject_warning = f"{fan_inject_warning}; IPMI fan inject skipped: {ipmi_error}".strip("; ")
                        else:
                            fan_inject_applied = True
                            fan_injected_backend = "ipmi"

            thermal_after_result = await ctx.redfish.get_thermal(bmc_host=bmc_host, verify_tls=verify_tls)
            if (
                not thermal_after_result.success
                and _is_unauthorized_error(_result_error(thermal_after_result))
                and not bmc_cfg.token
                and bmc_cfg.username
                and bmc_cfg.password
            ):
                reauth = await ctx.redfish.authenticate(
                    bmc_host=bmc_host,
                    username=bmc_cfg.username,
                    password=bmc_cfg.password,
                    verify_tls=verify_tls,
                )
                if reauth.success:
                    thermal_after_result = await ctx.redfish.get_thermal(bmc_host=bmc_host, verify_tls=verify_tls)
            if not thermal_after_result.success:
                return InjectResult(
                    success=False,
                    fault_id=ctx.fault_id,
                    error=f"Redfish thermal post-inject check failed: {_result_error(thermal_after_result)}",
                )
            thermal_after_payload = _parse_json_payload(thermal_after_result.output)

            ctx.params["bmc_precheck"] = {
                "bmc_host": bmc_host,
                "verify_tls": verify_tls,
                "fan_before": fan_before,
                "fan_after_inject": _fan_snapshot(thermal_after_payload),
                "temperature_before": temp_before,
                "temperature_after_inject": _temperature_snapshot(thermal_after_payload),
                "power_control_count": len(power_controls),
                "sensor_member_count": len(sensor_members),
                "sensor_query_success": bool(sensors.success),
                "fan_inject": {
                    "fan_index": fan_index,
                    "mode": fan_mode,
                    "pwm": fan_pwm,
                },
                "fan_control_backend": fan_backend,
                "fan_injected_backend": fan_injected_backend,
                "fan_inject_applied": fan_inject_applied,
                "fan_inject_warning": fan_inject_warning,
            }
            ctx.params["_fan_injected"] = fan_inject_applied
            ctx.params["_fan_injected_backend"] = fan_injected_backend
        finally:
            if created_session:
                logout = await ctx.redfish.logout(bmc_host=bmc_host, verify_tls=verify_tls)
                if not logout.success:
                    logger.warning("Redfish logout failed for %s: %s", bmc_host, logout.error)

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
        return InjectResult(success=False, fault_id=ctx.fault_id, error=_result_error(result))

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        gpu_id = int(ctx.params.get("gpu_id", 0))
        original_power = int(ctx.params.get("original_power", 300))
        injected_backend = str(ctx.params.get("_fan_injected_backend", "")).strip().lower()
        recover_cmd = f"nvidia-smi -i {gpu_id} -pl {original_power}"
        try:
            _guard_check(ctx, recover_cmd)
        except Exception as exc:
            _mark_recovered_or_failed(ctx, False)
            return RecoverResult(success=False, fault_id=ctx.fault_id, error=str(exc))

        result = await ctx.ssh.run_command(node=ctx.target_node, command=recover_cmd, use_sudo=True)
        ssh_recover_success = bool(result.success or result.dry_run)

        redfish_recover_success = True
        redfish_recover_error = ""
        bmc_cfg = ctx.target_redfish
        if ctx.params.get("_fan_injected") and injected_backend == "redfish" and ctx.redfish and bmc_cfg:
            bmc_host = bmc_cfg.bmc_host
            verify_tls = bool(bmc_cfg.verify_tls)
            created_session = False
            try:
                if bmc_cfg.token:
                    ctx.redfish.set_token(bmc_host, bmc_cfg.token)
                else:
                    if not bmc_cfg.username or not bmc_cfg.password:
                        redfish_recover_success = False
                        redfish_recover_error = "Missing Redfish credentials for fan recovery"
                    else:
                        auth = await ctx.redfish.authenticate(
                            bmc_host=bmc_host,
                            username=bmc_cfg.username,
                            password=bmc_cfg.password,
                            verify_tls=verify_tls,
                        )
                        if not auth.success:
                            redfish_recover_success = False
                            redfish_recover_error = f"Redfish auth failed in recovery: {auth.error}"
                        else:
                            created_session = True

                if redfish_recover_success:
                    pre = await ctx.redfish.get_thermal(bmc_host=bmc_host, verify_tls=verify_tls)
                    set_auto = await ctx.redfish.set_fan_control(
                        bmc_host=bmc_host,
                        fan_index=int(ctx.params.get("fan_index", 0)),
                        mode=str(ctx.params.get("fan_recover_mode", "Auto")),
                        pwm=None,
                        verify_tls=verify_tls,
                        fault_id=ctx.fault_id,
                    )
                    post = await ctx.redfish.get_thermal(bmc_host=bmc_host, verify_tls=verify_tls)
                    if not set_auto.success:
                        redfish_recover_success = False
                        redfish_recover_error = f"Redfish fan recovery failed: {_result_error(set_auto)}"
                    else:
                        pre_payload = _parse_json_payload(pre.output) if pre.success else {}
                        post_payload = _parse_json_payload(post.output) if post.success else {}
                        ctx.params["bmc_recover_snapshot"] = {
                            "fan_before_recover": _fan_snapshot(pre_payload),
                            "fan_after_recover": _fan_snapshot(post_payload),
                            "temperature_before_recover": _temperature_snapshot(pre_payload),
                            "temperature_after_recover": _temperature_snapshot(post_payload),
                        }
            finally:
                if created_session:
                    logout = await ctx.redfish.logout(bmc_host=bmc_host, verify_tls=verify_tls)
                    if not logout.success:
                        logger.warning("Redfish logout failed for %s during recovery: %s", bmc_host, logout.error)

        ipmi_recover_success = True
        ipmi_recover_error = ""
        if ctx.params.get("_fan_injected") and injected_backend == "ipmi":
            if not ctx.target_ipmi:
                ipmi_recover_success = False
                ipmi_recover_error = "Missing target IPMI config for fan recovery"
            else:
                ipmi_cfg = ctx.target_ipmi
                profile = str(ctx.params.get("ipmi_profile", "read_only")).strip().lower()
                _, auto_cmd = _build_ipmi_profile_commands(profile, int(ctx.params.get("ipmi_target_pwm", 30)))
                if not auto_cmd:
                    ipmi_recover_success = False
                    ipmi_recover_error = f"Unsupported IPMI profile for recovery: {profile}"
                else:
                    pre = await _ipmi_collect_snapshot(ctx, ipmi_cfg)
                    if not ctx.ipmi:
                        ipmi_recover_success = False
                        ipmi_recover_error = "IPMI channel not initialized"
                        post = await _ipmi_collect_snapshot(ctx, ipmi_cfg)
                        ctx.params["ipmi_recover_snapshot"] = {
                            "before": pre,
                            "after": post,
                            "command": auto_cmd,
                            "command_success": False,
                            "output": "",
                            "error": ipmi_recover_error,
                        }
                    else:
                        recover_result = await ctx.ipmi.raw_command(
                            host=str(ipmi_cfg.host),
                            username=str(ipmi_cfg.username or ""),
                            password=str(ipmi_cfg.password or ""),
                            netfn=int(auto_cmd["netfn"]),
                            command=int(auto_cmd["command"]),
                            data=[int(x) for x in auto_cmd.get("data", [])],
                            port=int(getattr(ipmi_cfg, "port", 623) or 623),
                            interface=str(getattr(ipmi_cfg, "interface", "lanplus") or "lanplus"),
                            timeout=int(getattr(ipmi_cfg, "timeout", 30) or 30),
                        )
                        ok = bool(recover_result.success)
                        out = recover_result.output
                        err = recover_result.error
                        post = await _ipmi_collect_snapshot(ctx, ipmi_cfg)
                        ctx.params["ipmi_recover_snapshot"] = {
                            "before": pre,
                            "after": post,
                            "command": auto_cmd,
                            "command_success": ok,
                            "output": out,
                            "error": err,
                        }
                        if not ok:
                            ipmi_recover_success = False
                            ipmi_recover_error = f"IPMI fan recovery failed: {err or out or auto_cmd}"

        success = bool(ssh_recover_success and redfish_recover_success and ipmi_recover_success)
        _mark_recovered_or_failed(ctx, success)
        if success:
            return RecoverResult(success=True, fault_id=ctx.fault_id)
        error = _result_error(result)
        if redfish_recover_error:
            if error:
                error = f"{error}; {redfish_recover_error}"
            else:
                error = redfish_recover_error
        if ipmi_recover_error:
            if error:
                error = f"{error}; {ipmi_recover_error}"
            else:
                error = ipmi_recover_error
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=error)

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
