"""Read-only BMC tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency at runtime
    yaml = None  # type: ignore[assignment]

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_channel(context: ToolExecutionContext) -> Any:
    channel = context.channels.get("redfish")
    if channel is None:
        raise ToolValidationError("required channel is missing: redfish")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _read_inventory_payload(path: Path) -> dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_inventory_candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    env_inventory = str(os.getenv("SRE_SSH_INVENTORY_PATH", "") or "").strip()
    if env_inventory:
        candidates.append(Path(env_inventory))

    env_config = str(os.getenv("SRE_AGENT_CONFIG", "") or "").strip()
    config_candidates = [Path(env_config)] if env_config else []
    config_candidates.append(Path("sre_agent/conf/config.yaml"))

    if yaml is not None:
        for config_path in config_candidates:
            if not config_path.exists():
                continue
            try:
                payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                continue
            raw_inventory_path = payload.get("ontology", {}).get("discovery", {}).get("live_inventory_path")
            if not raw_inventory_path:
                continue
            inventory_path = Path(str(raw_inventory_path))
            if not inventory_path.is_absolute():
                inventory_path = (config_path.parent.parent.parent / inventory_path).resolve()
            candidates.append(inventory_path)

    candidates.append(Path("sre_agent/conf/live_inventory.lab.yaml"))

    deduped: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _resolve_bmc_target(params: dict[str, Any]) -> tuple[str, bool]:
    bmc_host = str(params.get("bmc_host", "") or "").strip()
    verify_tls_param = params.get("verify_tls")
    if bmc_host:
        verify_tls = True if verify_tls_param is None else bool(verify_tls_param)
        return bmc_host, verify_tls

    node = str(params.get("node", "") or "").strip()
    if not node:
        raise ToolValidationError("parameter 'node' or 'bmc_host' is required")

    for path in _iter_inventory_candidate_paths():
        payload = _read_inventory_payload(path)
        workers = payload.get("inventory", {}).get("workers", [])
        if not isinstance(workers, list):
            continue
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            # Match by worker name, SSH host IP, or k8s_node_name
            worker_name = str(worker.get("name", "") or "").strip()
            k8s_node_name = str(worker.get("k8s_node_name", "") or "").strip()
            ssh_host = ""
            ssh_config = worker.get("ssh")
            if isinstance(ssh_config, dict):
                ssh_host = str(ssh_config.get("host", "") or "").strip()

            if worker_name != node and ssh_host != node and k8s_node_name != node:
                continue
            redfish = worker.get("redfish")
            if not isinstance(redfish, dict):
                continue
            bmc_host = str(redfish.get("bmc_host", "") or "").strip()
            if not bmc_host:
                continue
            if verify_tls_param is None:
                verify_tls = bool(True if redfish.get("verify_tls") is None else redfish.get("verify_tls"))
            else:
                verify_tls = bool(verify_tls_param)
            return bmc_host, verify_tls

    raise ToolValidationError(f"no BMC target mapping found for node {node!r}")


def _unwrap_channel_result(value: Any, *, action: str) -> Any:
    success = getattr(value, "success", None)
    if success is not None and not bool(success):
        error_text = str(getattr(value, "error", "") or "").strip()
        if error_text:
            raise ToolValidationError(error_text)
        raise ToolValidationError(f"channel action failed: {action}")
    data = getattr(value, "data", None)
    if data is not None:
        return data
    output = getattr(value, "output", None)
    if output is not None:
        return output
    return value


async def get_info(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    bmc_host = _require_str(params, "bmc_host")
    verify_tls = bool(params.get("verify_tls", True))
    if hasattr(channel, "get_bmc_info"):
        value = await channel.get_bmc_info(bmc_host, verify_tls=verify_tls)
        return _unwrap_channel_result(value, action="get_bmc_info")
    raise ToolValidationError("redfish channel does not support get_bmc_info")


async def get_thermal(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    bmc_host = _require_str(params, "bmc_host")
    verify_tls = bool(params.get("verify_tls", True))
    if hasattr(channel, "get_thermal"):
        value = await channel.get_thermal(bmc_host, verify_tls=verify_tls)
        return _unwrap_channel_result(value, action="get_thermal")
    raise ToolValidationError("redfish channel does not support get_thermal")


async def get_power(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    bmc_host = _require_str(params, "bmc_host")
    verify_tls = bool(params.get("verify_tls", True))
    if hasattr(channel, "get_power"):
        value = await channel.get_power(bmc_host, verify_tls=verify_tls)
        return _unwrap_channel_result(value, action="get_power")
    raise ToolValidationError("redfish channel does not support get_power")


def _summarize_fan_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"mode_name": "Unknown", "is_manual": False, "is_auto": False, "is_fixed_pwm": False}

    fan_mode_raw = payload.get("fanMode")
    try:
        fan_mode = int(fan_mode_raw)
    except (TypeError, ValueError):
        fan_mode = None
    mode_name = {0: "Auto", 1: "Manual"}.get(fan_mode, "Unknown")

    all_fans = payload.get("allFanInfo")
    pwm_values: list[int] = []
    if isinstance(all_fans, list):
        for backplane in all_fans:
            if not isinstance(backplane, dict):
                continue
            for fan in backplane.get("fanInfo") or []:
                if not isinstance(fan, dict):
                    continue
                try:
                    pwm_values.append(int(fan.get("fanPWM")))
                except (TypeError, ValueError):
                    continue

    unique_pwm_values = sorted(set(pwm_values))
    return {
        "mode_name": mode_name,
        "is_manual": fan_mode == 1,
        "is_auto": fan_mode == 0,
        "is_fixed_pwm": len(unique_pwm_values) == 1 and bool(unique_pwm_values),
        "fixed_pwm": unique_pwm_values[0] if len(unique_pwm_values) == 1 else None,
        "unique_pwm_values": unique_pwm_values,
        "fan_count": len(pwm_values),
    }


async def get_fan_status(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    bmc_host, verify_tls = _resolve_bmc_target(params)
    if hasattr(channel, "get_web_fan_status"):
        value = await channel.get_web_fan_status(bmc_host, verify_tls=verify_tls)
        payload = _unwrap_channel_result(value, action="get_web_fan_status")
        # Parse JSON string if needed (BMC API may return raw JSON string)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        node = str(params.get("node", "") or "").strip() or None
        return {
            "node": node,
            "bmc_host": bmc_host,
            "fan_status_summary": _summarize_fan_status(payload),
            "fan_status": payload,
        }
    raise ToolValidationError("redfish channel does not support get_web_fan_status")
