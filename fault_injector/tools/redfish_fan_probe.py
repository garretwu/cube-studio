from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# Ensure repository root is importable when run as a script file.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.fchannels.redfish import RedfishChannel


KEYWORDS = ("fan", "thermal", "cool", "pwm", "rpm", "temperature", "temp")
WRITE_HINT_PATHS = (
    "/redfish/v1/Chassis/Self/Thermal/ThermalManagement",
    "/redfish/v1/Chassis/Self/ThermalSubsystem",
    "/redfish/v1/Chassis/Self/ThermalSubsystem/Fans",
)
VALUE_HINT_KEYS = (
    "FanControlMode",
    "FanPWM",
    "Reading",
    "ReadingUnits",
    "ReadingCelsius",
    "LowerThresholdCritical",
    "UpperThresholdCritical",
)


@dataclass
class ProbeConfig:
    host: str
    username: str
    password: str
    verify_tls: bool
    max_depth: int
    max_nodes: int
    timeout: int
    probe_writes: bool
    candidate_limit: int
    oem_max_depth: int
    oem_max_nodes: int
    output_path: Path | None


def _contains_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in KEYWORDS)


def _extract_links(payload: Any) -> set[str]:
    links: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            odata = node.get("@odata.id")
            if (
                isinstance(odata, str)
                and odata.startswith("/redfish")
                and "#" not in odata
                and "@" not in odata
            ):
                links.add(odata)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return links


def _extract_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions_node = payload.get("Actions")
    if not isinstance(actions_node, dict):
        return []
    actions: list[dict[str, Any]] = []
    for action_name, action_value in actions_node.items():
        if not isinstance(action_name, str):
            continue
        target = ""
        allowable = []
        if isinstance(action_value, dict):
            target = str(action_value.get("target", ""))
            values = action_value.get("ResetType@Redfish.AllowableValues", [])
            if isinstance(values, list):
                allowable = [str(v) for v in values]
        actions.append(
            {
                "action": action_name,
                "target": target,
                "allowable_values": allowable,
                "fan_thermal_related": _contains_keyword(action_name) or _contains_keyword(target),
            }
        )
    return actions


def _extract_oem(payload: dict[str, Any]) -> dict[str, Any]:
    oem = payload.get("Oem")
    if isinstance(oem, dict):
        return oem
    return {}


def _normalize_action_target(host: str, target: str) -> str:
    if not target:
        return ""
    if target.startswith("/redfish"):
        return target
    if target.startswith(f"https://{host}"):
        return target[len(f"https://{host}") :]
    return ""


def _is_oem_path(path: str) -> bool:
    lowered = path.lower()
    return "/oem/" in lowered or "ami" in lowered


def _extract_key_links(payload: Any) -> set[str]:
    links: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, dict):
                    odata = value.get("@odata.id")
                    if isinstance(odata, str) and odata.startswith("/redfish"):
                        links.add(odata)
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return links


def _resource_summary(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    actions = _extract_actions(payload)
    oem = _extract_oem(payload)
    related_keys = [key for key in payload.keys() if _contains_keyword(str(key))]
    hint_values: dict[str, Any] = {}
    for key in VALUE_HINT_KEYS:
        if key in payload:
            hint_values[key] = payload.get(key)

    oem_json = json.dumps(oem, ensure_ascii=False) if oem else ""
    path_related = _contains_keyword(path)
    has_related_action = any(a["fan_thermal_related"] for a in actions)
    has_related_oem = _contains_keyword(oem_json) if oem_json else False
    related = path_related or bool(related_keys) or has_related_action or has_related_oem

    return {
        "path": path,
        "odata_type": payload.get("@odata.type"),
        "name": payload.get("Name"),
        "id": payload.get("Id"),
        "related": related,
        "related_keys": related_keys,
        "actions": actions,
        "value_hints": hint_values,
        "oem": oem,
    }


def _collect_write_candidates(host: str, resources: list[dict[str, Any]], candidate_limit: int) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    for hint in WRITE_HINT_PATHS:
        candidates[hint] = {"path": hint, "source": "hint_path", "kind": "resource"}

    for item in resources:
        path = str(item.get("path", ""))
        if _contains_keyword(path):
            candidates.setdefault(path, {"path": path, "source": "resource_path", "kind": "resource"})

        for action in item.get("actions", []):
            target = _normalize_action_target(host, str(action.get("target", "")))
            if not target:
                continue
            is_related = bool(action.get("fan_thermal_related")) or _contains_keyword(target)
            if not is_related:
                continue
            candidates.setdefault(
                target,
                {
                    "path": target,
                    "source": "action_target",
                    "kind": "action",
                    "action": str(action.get("action", "")),
                },
            )

    selected = sorted(candidates.values(), key=lambda c: (c["kind"], c["path"]))
    return selected[:candidate_limit]


async def _probe_http(
    client: httpx.AsyncClient,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"X-Auth-Token": token}
    try:
        response = await client.request(method=method, url=path, headers=headers, json=payload)
        body = response.text.strip()
        if len(body) > 240:
            body = body[:240] + "...(truncated)"
        return {
            "method": method,
            "path": path,
            "status_code": response.status_code,
            "ok": response.is_success,
            "body_excerpt": body,
            "error": "",
        }
    except Exception as exc:
        return {
            "method": method,
            "path": path,
            "status_code": None,
            "ok": False,
            "body_excerpt": "",
            "error": str(exc),
        }


async def _run_write_probe(
    cfg: ProbeConfig,
    token: str,
    resources: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = _collect_write_candidates(cfg.host, resources, cfg.candidate_limit)
    results: list[dict[str, Any]] = []
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    async with httpx.AsyncClient(
        base_url=f"https://{cfg.host}",
        timeout=cfg.timeout,
        verify=cfg.verify_tls,
        limits=limits,
    ) as client:
        for candidate in candidates:
            path = candidate["path"]
            entry: dict[str, Any] = {
                "path": path,
                "source": candidate.get("source"),
                "kind": candidate.get("kind"),
                "action": candidate.get("action", ""),
                "probes": [],
            }
            entry["probes"].append(await _probe_http(client, token, "GET", path))
            entry["probes"].append(await _probe_http(client, token, "OPTIONS", path))
            if cfg.probe_writes:
                method = "POST" if candidate.get("kind") == "action" else "PATCH"
                entry["probes"].append(await _probe_http(client, token, method, path, payload={}))
            results.append(entry)

    writable_like = []
    for item in results:
        for probe in item["probes"]:
            code = probe.get("status_code")
            if code in (200, 201, 202, 204, 400, 401, 403, 405, 409):
                writable_like.append({"path": item["path"], "probe": probe})
                break

    return {
        "enabled": cfg.probe_writes,
        "candidate_count": len(candidates),
        "results": results,
        "writable_like": writable_like,
    }


def _collect_oem_seed_paths(resources: list[dict[str, Any]]) -> list[str]:
    seeds = {
        "/redfish/v1/Oem/Ami/Configurations",
        "/redfish/v1/AccountService/Oem/Ami/Configurations",
    }
    for item in resources:
        path = str(item.get("path", ""))
        if _is_oem_path(path):
            seeds.add(path)
    return sorted(seeds)


def _collect_oem_write_candidates(host: str, resources: list[dict[str, Any]], candidate_limit: int) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for item in resources:
        path = str(item.get("path", ""))
        key_hits = [k for k in item.get("keys", []) if _contains_keyword(str(k))]
        if _is_oem_path(path) and key_hits:
            candidates.setdefault(
                path,
                {
                    "path": path,
                    "source": "oem_resource",
                    "kind": "resource",
                    "key_hits": key_hits,
                },
            )
        for action in item.get("actions", []):
            target = _normalize_action_target(host, str(action.get("target", "")))
            if not target:
                continue
            if _is_oem_path(target) or bool(action.get("fan_thermal_related")):
                candidates.setdefault(
                    target,
                    {
                        "path": target,
                        "source": "oem_action_target",
                        "kind": "action",
                        "action": str(action.get("action", "")),
                    },
                )
    selected = sorted(candidates.values(), key=lambda c: (c["kind"], c["path"]))
    return selected[:candidate_limit]


async def _run_oem_stage2_probe(
    channel: RedfishChannel,
    cfg: ProbeConfig,
    token: str,
    resources: list[dict[str, Any]],
) -> dict[str, Any]:
    seeds = _collect_oem_seed_paths(resources)
    queue: deque[tuple[str, int]] = deque((s, 0) for s in seeds)
    visited: set[str] = set()
    discovered: list[dict[str, Any]] = []
    errors: list[str] = []

    while queue and len(visited) < cfg.oem_max_nodes:
        path, depth = queue.popleft()
        if path in visited or depth > cfg.oem_max_depth:
            continue
        visited.add(path)

        ok, payload, error = await _get_json(channel, cfg.host, cfg.verify_tls, path)
        if not ok:
            errors.append(f"{path}: {error}")
            continue

        actions = _extract_actions(payload)
        keys = sorted(payload.keys())
        discovered.append(
            {
                "path": path,
                "odata_type": payload.get("@odata.type"),
                "name": payload.get("Name"),
                "id": payload.get("Id"),
                "keys": keys,
                "actions": actions,
            }
        )

        links = _extract_links(payload) | _extract_key_links(payload)
        for link in sorted(links):
            if link in visited:
                continue
            if _is_oem_path(link) or _contains_keyword(link):
                queue.append((link, depth + 1))

    write_candidates = _collect_oem_write_candidates(cfg.host, discovered, cfg.candidate_limit)
    write_results: list[dict[str, Any]] = []
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    async with httpx.AsyncClient(
        base_url=f"https://{cfg.host}",
        timeout=cfg.timeout,
        verify=cfg.verify_tls,
        limits=limits,
    ) as client:
        for candidate in write_candidates:
            path = candidate["path"]
            entry: dict[str, Any] = {
                "path": path,
                "source": candidate.get("source"),
                "kind": candidate.get("kind"),
                "action": candidate.get("action", ""),
                "probes": [],
            }
            entry["probes"].append(await _probe_http(client, token, "GET", path))
            entry["probes"].append(await _probe_http(client, token, "OPTIONS", path))
            if cfg.probe_writes:
                method = "POST" if candidate.get("kind") == "action" else "PATCH"
                entry["probes"].append(await _probe_http(client, token, method, path, payload={}))
            write_results.append(entry)

    return {
        "seed_paths": seeds,
        "visited_count": len(visited),
        "resource_count": len(discovered),
        "errors": errors,
        "resources": discovered,
        "write_probe": {
            "enabled": cfg.probe_writes,
            "candidate_count": len(write_candidates),
            "results": write_results,
        },
    }


async def _get_json(channel: RedfishChannel, host: str, verify_tls: bool, path: str) -> tuple[bool, dict[str, Any], str]:
    result = await channel.request(
        bmc_host=host,
        method="GET",
        path=path,
        verify_tls=verify_tls,
        requires_auth=True,
    )
    if not result.success:
        return False, {}, result.error
    try:
        payload = json.loads(result.output) if result.output else {}
    except json.JSONDecodeError as exc:
        return False, {}, f"invalid JSON payload: {exc}"
    if not isinstance(payload, dict):
        return False, {}, "payload is not a JSON object"
    return True, payload, ""


async def run_probe(cfg: ProbeConfig) -> dict[str, Any]:
    channel = RedfishChannel(dry_run=False, timeout=cfg.timeout)
    report: dict[str, Any] = {
        "target": cfg.host,
        "started_at": datetime.now().isoformat(),
        "max_depth": cfg.max_depth,
        "max_nodes": cfg.max_nodes,
        "visited_count": 0,
        "errors": [],
        "resources": [],
        "fan_thermal_candidates": [],
        "write_probe": {},
        "oem_stage2": {},
    }

    auth = await channel.authenticate(
        bmc_host=cfg.host,
        username=cfg.username,
        password=cfg.password,
        verify_tls=cfg.verify_tls,
    )
    if not auth.success:
        report["errors"].append(f"authenticate failed: {auth.error}")
        await channel.close()
        report["finished_at"] = datetime.now().isoformat()
        return report
    token = auth.output

    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque(
        [
            ("/redfish/v1/", 0),
            ("/redfish/v1/Chassis/Self", 1),
            ("/redfish/v1/Chassis/Self/Thermal", 1),
            ("/redfish/v1/Chassis/Self/Power", 1),
            ("/redfish/v1/Chassis/Self/Sensors", 1),
            ("/redfish/v1/Managers/Self", 1),
            ("/redfish/v1/Systems/Self", 1),
        ]
    )

    try:
        while queue and len(visited) < cfg.max_nodes:
            path, depth = queue.popleft()
            if path in visited:
                continue
            if depth > cfg.max_depth:
                continue
            visited.add(path)

            ok, payload, error = await _get_json(channel, cfg.host, cfg.verify_tls, path)
            if not ok:
                report["errors"].append(f"{path}: {error}")
                continue

            summary = _resource_summary(path, payload)
            report["resources"].append(summary)
            if summary["related"]:
                report["fan_thermal_candidates"].append(summary)

            for link in sorted(_extract_links(payload)):
                if link not in visited:
                    queue.append((link, depth + 1))
        report["write_probe"] = await _run_write_probe(cfg, token, report["resources"])
        report["oem_stage2"] = await _run_oem_stage2_probe(channel, cfg, token, report["resources"])
    finally:
        logout = await channel.logout(bmc_host=cfg.host, verify_tls=cfg.verify_tls)
        if not logout.success:
            report["errors"].append(f"logout failed: {logout.error}")
        await channel.close()

    report["visited_count"] = len(visited)
    report["candidate_count"] = len(report["fan_thermal_candidates"])
    report["finished_at"] = datetime.now().isoformat()
    return report


def _print_human_summary(report: dict[str, Any]) -> None:
    print(f"Target: {report['target']}")
    print(f"Visited resources: {report['visited_count']}")
    print(f"Fan/Thermal candidates: {report.get('candidate_count', 0)}")
    if report["errors"]:
        print("Errors:")
        for err in report["errors"][:10]:
            print(f"  - {err}")

    print("\nTop candidates:")
    for item in report.get("fan_thermal_candidates", [])[:20]:
        print(f"- {item.get('path')}")
        actions = item.get("actions", [])
        for action in actions:
            if action.get("fan_thermal_related"):
                print(f"    action: {action.get('action')} target={action.get('target')}")
        if item.get("related_keys"):
            print(f"    keys: {', '.join(item['related_keys'][:8])}")

    write_probe = report.get("write_probe", {})
    if write_probe:
        print("\nWrite probe:")
        print(f"  candidates: {write_probe.get('candidate_count', 0)}")
        print(f"  mode: {'active-write' if write_probe.get('enabled') else 'safe-discovery'}")
        for item in write_probe.get("results", [])[:12]:
            probe_summary = ", ".join(
                f"{probe.get('method')}={probe.get('status_code')}" for probe in item.get("probes", [])
            )
            print(f"  - {item.get('path')} [{probe_summary}]")

    stage2 = report.get("oem_stage2", {})
    if stage2:
        print("\nOEM stage2:")
        print(f"  visited: {stage2.get('visited_count', 0)} resources: {stage2.get('resource_count', 0)}")
        if stage2.get("errors"):
            print(f"  errors: {len(stage2['errors'])}")
        s2_write = stage2.get("write_probe", {})
        print(f"  write candidates: {s2_write.get('candidate_count', 0)}")
        for item in s2_write.get("results", [])[:12]:
            probe_summary = ", ".join(
                f"{probe.get('method')}={probe.get('status_code')}" for probe in item.get("probes", [])
            )
            print(f"  - {item.get('path')} [{probe_summary}]")


def parse_args() -> ProbeConfig:
    parser = argparse.ArgumentParser(description="Probe Redfish fan/thermal writable endpoints")
    parser.add_argument("--host", required=True, help="BMC host/IP")
    parser.add_argument("--username", required=True, help="Redfish username")
    parser.add_argument("--password", required=True, help="Redfish password")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification")
    parser.add_argument("--max-depth", type=int, default=4, help="Max crawl depth")
    parser.add_argument("--max-nodes", type=int, default=300, help="Max resources to crawl")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")
    parser.add_argument(
        "--probe-writes",
        action="store_true",
        help="Attempt lightweight write probes (PATCH/POST with empty JSON payload).",
    )
    parser.add_argument("--candidate-limit", type=int, default=40, help="Max write candidates to probe")
    parser.add_argument("--oem-max-depth", type=int, default=6, help="Max crawl depth for OEM stage2 probe")
    parser.add_argument("--oem-max-nodes", type=int, default=200, help="Max resources for OEM stage2 probe")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args()
    return ProbeConfig(
        host=args.host,
        username=args.username,
        password=args.password,
        verify_tls=not args.insecure,
        max_depth=args.max_depth,
        max_nodes=args.max_nodes,
        timeout=args.timeout,
        probe_writes=args.probe_writes,
        candidate_limit=args.candidate_limit,
        oem_max_depth=args.oem_max_depth,
        oem_max_nodes=args.oem_max_nodes,
        output_path=args.output,
    )


def main() -> None:
    cfg = parse_args()
    report = asyncio.run(run_probe(cfg))
    _print_human_summary(report)

    if cfg.output_path is not None:
        cfg.output_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved JSON report: {cfg.output_path}")


if __name__ == "__main__":
    main()
