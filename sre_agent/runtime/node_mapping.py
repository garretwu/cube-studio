from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency at runtime
    yaml = None  # type: ignore[assignment]


def map_internal_to_external_ip(ip: str) -> str | None:
    value = str(ip or "").strip()
    matched = re.fullmatch(r"10\.11\.0\.(\d{1,3})", value)
    if not matched:
        return None
    tail = int(matched.group(1))
    if tail < 0 or tail > 255:
        return None
    return f"10.11.4.{tail}"


def load_ip_mapping_overrides_from_env() -> dict[str, str]:
    raw = os.getenv("SRE_NODE_IP_MAPPING_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, str] = {}
    for source, target in payload.items():
        source_text = str(source or "").strip()
        target_text = str(target or "").strip()
        if not source_text or not target_text:
            continue
        normalized[source_text] = target_text
    return normalized


def iter_inventory_candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    env_inventory = os.getenv("SRE_SSH_INVENTORY_PATH", "").strip()
    if env_inventory:
        candidates.append(Path(env_inventory))

    config_candidates = [
        Path(os.getenv("SRE_AGENT_CONFIG", "").strip()) if os.getenv("SRE_AGENT_CONFIG", "").strip() else None,
        Path("sre_agent/conf/config.yaml"),
    ]
    for config_path in config_candidates:
        if config_path is None or not config_path.exists() or yaml is None:
            continue
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        raw_inventory_path = (
            payload.get("ontology", {})
            .get("discovery", {})
            .get("live_inventory_path")
        )
        if not raw_inventory_path:
            continue
        inventory_path = Path(str(raw_inventory_path))
        if not inventory_path.is_absolute():
            if inventory_path.exists():
                candidates.append(inventory_path)
            else:
                candidates.append((config_path.parent.parent.parent / inventory_path).resolve())
        else:
            candidates.append(inventory_path)

    candidates.append(Path("sre_agent/conf/live_inventory.lab.yaml"))

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        deduped.append(candidate)
        seen.add(key)
    return deduped


def load_inventory_node_mapping() -> tuple[set[str], dict[str, str]]:
    names: set[str] = set()
    host_to_name: dict[str, str] = {}
    k8s_node_to_name: dict[str, str] = {}
    if yaml is None:
        return names, host_to_name

    for path in iter_inventory_candidate_paths():
        if not path.exists():
            continue
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        workers = payload.get("inventory", {}).get("workers", [])
        if not isinstance(workers, list):
            continue
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            name = str(worker.get("name") or "").strip()
            ssh = worker.get("ssh")
            host = str(ssh.get("host") or "").strip() if isinstance(ssh, dict) else ""
            k8s_node_name = str(worker.get("k8s_node_name") or "").strip()
            if name:
                names.add(name)
            if name and host:
                host_to_name[host] = name
            if name and k8s_node_name:
                k8s_node_to_name[k8s_node_name] = name
        if names or host_to_name:
            break
    # Merge k8s_node_to_name into host_to_name for unified lookup
    for k8s_name, worker_name in k8s_node_to_name.items():
        if k8s_name not in host_to_name:
            host_to_name[k8s_name] = worker_name
    return names, host_to_name


def normalize_node_identifier(
    node: str,
    *,
    inventory_names: set[str],
    host_to_name: dict[str, str],
) -> tuple[str | None, str | None]:
    raw = str(node or "").strip()
    if not raw:
        return None, "missing_node"

    if raw in inventory_names:
        return raw, None
    if raw in host_to_name:
        return host_to_name[raw], None

    overrides = load_ip_mapping_overrides_from_env()
    mapped_override = overrides.get(raw)
    if mapped_override:
        mapped_override = mapped_override.strip()
        if mapped_override in inventory_names:
            return mapped_override, None
        if mapped_override in host_to_name:
            return host_to_name[mapped_override], None

    mapped_ip = map_internal_to_external_ip(raw)
    if mapped_ip:
        if mapped_ip in host_to_name:
            return host_to_name[mapped_ip], None
        if mapped_ip in inventory_names:
            return mapped_ip, None
        return None, f"mapped_host_not_found:{mapped_ip}"

    return None, f"node_not_in_inventory:{raw}"
