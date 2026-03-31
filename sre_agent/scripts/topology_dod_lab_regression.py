#!/usr/bin/env python3
"""Lab profile wrapper for topology DoD regression.

This wrapper pins:
1) sre_agent/conf/config.lab.yaml as topology asset source
2) fixed asset labels (switch + switch_port ids derived from lab config)
3) acceptance thresholds for CI gates
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from sre_agent.auth.jwt import CurrentUser, JWTSettings, encode_token
from sre_agent.config import load_config
from sre_agent.scripts.topology_dod_real_drill import (
    DrillArgs,
    _load_runtime_info,
    _resolve_base_url,
    _resolve_token,
    _run,
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _wait_backend_ready(base_url: str, *, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with httpx.Client(base_url=base_url, timeout=4.0, trust_env=False) as client:
                resp = client.get("/openapi.json")
                if resp.status_code == 200:
                    paths = resp.json().get("paths", {})
                    if "/api/topology/status" in paths and "/api/topology/discover" in paths:
                        return
                    last_error = "openapi ready but topology status/discover paths are missing"
                else:
                    last_error = f"/openapi.json status={resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"backend is not ready within {timeout_seconds}s: {last_error}")


def _extract_expected_assets_from_config(config_path: Path) -> list[str]:
    cfg = load_config(config_path)
    expected: list[str] = []
    live_inventory_path = Path(str(cfg.ontology.discovery.live_inventory_path)).expanduser()
    if not live_inventory_path.is_absolute():
        live_inventory_path = (config_path.parent / live_inventory_path).resolve()

    if live_inventory_path.exists():
        try:
            payload = yaml.safe_load(live_inventory_path.read_text(encoding="utf-8")) or {}
        except Exception:
            payload = {}
        workers = payload.get("inventory", {}).get("workers", []) if isinstance(payload, dict) else []
        if isinstance(workers, list):
            for worker in workers:
                if not isinstance(worker, dict):
                    continue
                name = str(worker.get("name", "")).strip()
                if name and name not in expected:
                    expected.append(name)

    for switch in cfg.ontology.discovery.switches:
        switch_id = (switch.id or switch.name).strip()
        if switch_id and switch_id not in expected:
            expected.append(switch_id)
        for port in switch.ports:
            port_id = (port.id or f"{switch_id}:{port.name}").strip()
            if port_id and port_id not in expected:
                expected.append(port_id)
    cluster_name = str(cfg.ontology.discovery.k8s_cluster_name).strip()
    if cluster_name:
        cluster_id = cluster_name if cluster_name.startswith("k8s:") else f"k8s:{cluster_name}"
        if cluster_id not in expected:
            expected.append(cluster_id)
    return expected


def _extract_topology_node_ids(report_dir: Path) -> list[str]:
    exchanges_path = report_dir / "api" / "http_exchanges.json"
    if not exchanges_path.exists():
        return []
    try:
        exchanges = json.loads(exchanges_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(exchanges, list):
        return []
    for item in exchanges:
        if not isinstance(item, dict):
            continue
        if item.get("path") != "/api/topology":
            continue
        body = item.get("response_body", {})
        if not isinstance(body, dict):
            continue
        data = body.get("data", {})
        if not isinstance(data, dict):
            continue
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        ids: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id", "")).strip()
            if node_id:
                ids.append(node_id)
        if ids:
            return ids
    return []


def _gate_check(
    *,
    summary: dict[str, Any],
    expected_assets: list[str],
    observed_node_ids: list[str],
    min_discovery_success_rate: float,
    max_refresh_p95_seconds: float,
    min_ws_reconnect_success_rate: float,
    max_node_missing_rate: float,
    min_blast_radius_hit_rate: float,
    min_asset_presence_rate: float,
) -> dict[str, Any]:
    def _as_float(value: Any, default: float) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    metrics = summary.get("metrics", {}) if isinstance(summary, dict) else {}
    checks = summary.get("checks", {}) if isinstance(summary, dict) else {}

    discovery_success_rate = _as_float(metrics.get("discovery_success_rate"), 0.0)
    refresh_p95 = metrics.get("refresh_duration_seconds_p95")
    ws_reconnect_success_rate = _as_float(metrics.get("ws_reconnect_success_rate"), 0.0)
    node_missing_rate = _as_float(metrics.get("node_missing_rate"), 1.0)
    blast_radius_hit_rate = _as_float(metrics.get("blast_radius_hit_rate"), 0.0)

    observed_set = set(observed_node_ids)
    expected_set = set(expected_assets)
    found_assets = sorted(observed_set & expected_set)
    missing_assets = sorted(expected_set - observed_set)
    asset_presence_rate = (len(found_assets) / len(expected_assets)) if expected_assets else 0.0

    threshold_results = {
        "check_overall_dod": bool(checks.get("overall_pass")),
        "check_discovery_success_rate": discovery_success_rate >= min_discovery_success_rate,
        "check_refresh_p95_seconds": isinstance(refresh_p95, (int, float)) and float(refresh_p95) <= max_refresh_p95_seconds,
        "check_ws_reconnect_success_rate": ws_reconnect_success_rate >= min_ws_reconnect_success_rate,
        "check_node_missing_rate": node_missing_rate <= max_node_missing_rate,
        "check_blast_radius_hit_rate": blast_radius_hit_rate >= min_blast_radius_hit_rate,
        "check_asset_presence_rate": asset_presence_rate >= min_asset_presence_rate,
    }

    pass_all = all(threshold_results.values())
    return {
        "pass": pass_all,
        "thresholds": {
            "min_discovery_success_rate": min_discovery_success_rate,
            "max_refresh_p95_seconds": max_refresh_p95_seconds,
            "min_ws_reconnect_success_rate": min_ws_reconnect_success_rate,
            "max_node_missing_rate": max_node_missing_rate,
            "min_blast_radius_hit_rate": min_blast_radius_hit_rate,
            "min_asset_presence_rate": min_asset_presence_rate,
        },
        "actuals": {
            "discovery_success_rate": discovery_success_rate,
            "refresh_duration_seconds_p95": refresh_p95,
            "ws_reconnect_success_rate": ws_reconnect_success_rate,
            "node_missing_rate": node_missing_rate,
            "blast_radius_hit_rate": blast_radius_hit_rate,
            "asset_presence_rate": asset_presence_rate,
        },
        "threshold_results": threshold_results,
        "assets": {
            "expected": expected_assets,
            "observed_node_ids": observed_node_ids,
            "found_assets": found_assets,
            "missing_assets": missing_assets,
        },
    }


def _build_backend_command(config_path: Path, host: str, port: int, log_level: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "sre_agent",
        "serve",
        "--config",
        str(config_path),
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        log_level,
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lab-profile topology DoD regression with fixed thresholds.")
    parser.add_argument("--config", default="sre_agent/conf/config.lab.yaml", help="Lab config path.")
    parser.add_argument("--runtime-info", default="sre_agent/temp/dev_runtime_info.json")
    parser.add_argument("--base-url", default="", help="Backend URL. Empty = runtime-info/env/default.")
    parser.add_argument("--token", default="", help="Bearer token. Empty = runtime-info/env/fallback.")
    parser.add_argument("--role", choices=["viewer", "operator", "admin"], default="operator")
    parser.add_argument("--user", default="topology-lab-ci")
    parser.add_argument("--evidence-root", default="sre_agent/docs/evidence")
    parser.add_argument("--run-id", default="", help="Default: YYYYMMDD-HHMM-topology-dod-lab")
    parser.add_argument("--request-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--periodic-poll-seconds", type=int, default=5)
    parser.add_argument("--periodic-wait-seconds", type=int, default=0, help="0 = derive from config refresh interval")
    parser.add_argument("--ws-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--ws-max-events", type=int, default=120)

    parser.add_argument("--start-backend", action="store_true", help="Start backend process for this run.")
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=18090)
    parser.add_argument("--backend-log-level", default="info")
    parser.add_argument("--backend-ready-timeout-seconds", type=int, default=90)

    parser.add_argument("--min-discovery-success-rate", type=float, default=1.0)
    parser.add_argument("--max-refresh-p95-seconds", type=float, default=10.0)
    parser.add_argument("--min-ws-reconnect-success-rate", type=float, default=1.0)
    parser.add_argument("--max-node-missing-rate", type=float, default=0.0)
    parser.add_argument("--min-blast-radius-hit-rate", type=float, default=1.0)
    parser.add_argument("--min-asset-presence-rate", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        raise SystemExit(f"missing config file: {config_path}")
    cfg = load_config(config_path)
    refresh_interval = max(30, int(cfg.ontology.discovery.refresh_interval_seconds))
    periodic_wait_seconds = (
        max(30, int(args.periodic_wait_seconds)) if int(args.periodic_wait_seconds) > 0 else refresh_interval + 45
    )

    runtime_info = _load_runtime_info(Path(args.runtime_info))
    base_url = _resolve_base_url(args.base_url, runtime_info)
    if args.start_backend:
        base_url = f"http://{args.backend_host}:{args.backend_port}"

    token = ""
    backend_proc: subprocess.Popen[bytes] | None = None

    env = dict(os.environ)
    if args.start_backend:
        secret_env = cfg.auth.jwt_secret_env
        secret_value = env.get(secret_env)
        if not secret_value:
            secret_value = secrets.token_urlsafe(48)
            env[secret_env] = secret_value
            settings = JWTSettings(
                secret=secret_value,
                algorithm=cfg.auth.jwt_algorithm,
                audience=cfg.auth.audience,
                expire_seconds=8 * 3600,
            )
        settings = JWTSettings(
            secret=secret_value,
            algorithm=cfg.auth.jwt_algorithm,
            audience=cfg.auth.audience,
            expire_seconds=8 * 3600,
        )
        token = args.token.strip() or encode_token(
            CurrentUser(user_id="topology-lab-ci", username=args.user, role=args.role),  # type: ignore[arg-type]
            settings,
        )
        cmd = _build_backend_command(config_path, args.backend_host, args.backend_port, args.backend_log_level)
        backend_proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env)
        try:
            _wait_backend_ready(base_url, timeout_seconds=max(20, int(args.backend_ready_timeout_seconds)))
        except Exception:
            if backend_proc.poll() is None:
                backend_proc.terminate()
            raise
    else:
        token = _resolve_token(args.token, runtime_info, args.role, args.user)

    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%d-%H%M')}-topology-dod-lab"
    drill_args = DrillArgs(
        base_url=base_url,
        token=token,
        role=args.role,
        user=args.user,
        request_timeout_seconds=max(5.0, float(args.request_timeout_seconds)),
        periodic_wait_seconds=periodic_wait_seconds,
        periodic_poll_seconds=max(2, int(args.periodic_poll_seconds)),
        ws_timeout_seconds=max(2.0, float(args.ws_timeout_seconds)),
        ws_max_events=max(10, int(args.ws_max_events)),
        evidence_root=Path(args.evidence_root),
        run_id=run_id,
    )

    try:
        summary, _ = asyncio.run(_run(drill_args))
    finally:
        if backend_proc is not None and backend_proc.poll() is None:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=12)
            except subprocess.TimeoutExpired:
                backend_proc.kill()
                backend_proc.wait(timeout=5)

    run_dir = Path(args.evidence_root) / run_id
    expected_assets = _extract_expected_assets_from_config(config_path)
    observed_nodes = _extract_topology_node_ids(run_dir)
    gate = _gate_check(
        summary=summary,
        expected_assets=expected_assets,
        observed_node_ids=observed_nodes,
        min_discovery_success_rate=float(args.min_discovery_success_rate),
        max_refresh_p95_seconds=float(args.max_refresh_p95_seconds),
        min_ws_reconnect_success_rate=float(args.min_ws_reconnect_success_rate),
        max_node_missing_rate=float(args.max_node_missing_rate),
        min_blast_radius_hit_rate=float(args.min_blast_radius_hit_rate),
        min_asset_presence_rate=float(args.min_asset_presence_rate),
    )

    drill_args_payload = {
        "base_url": drill_args.base_url,
        "token": "<redacted>",
        "role": drill_args.role,
        "user": drill_args.user,
        "request_timeout_seconds": drill_args.request_timeout_seconds,
        "periodic_wait_seconds": drill_args.periodic_wait_seconds,
        "periodic_poll_seconds": drill_args.periodic_poll_seconds,
        "ws_timeout_seconds": drill_args.ws_timeout_seconds,
        "ws_max_events": drill_args.ws_max_events,
        "evidence_root": str(drill_args.evidence_root),
        "run_id": drill_args.run_id,
    }
    merged = {
        "profile": "lab",
        "config_path": str(config_path),
        "generated_at": _now_utc().isoformat(),
        "run_id": run_id,
        "drill_args": drill_args_payload,
        "summary": summary,
        "gate": gate,
    }
    report_dir = run_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "topology_dod_lab_gate_report.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        f"# Topology DoD Lab Gate Report ({run_id})",
        "",
        f"- generated_at: {merged['generated_at']}",
        f"- config_path: {config_path}",
        f"- base_url: {base_url}",
        f"- gate_pass: {gate['pass']}",
        "",
        "## Threshold Results",
    ]
    for key, value in gate["threshold_results"].items():
        lines.append(f"- {key}: {value}")
    lines += [
        "",
        "## Asset Coverage",
        f"- expected_assets: {len(gate['assets']['expected'])}",
        f"- found_assets: {len(gate['assets']['found_assets'])}",
        f"- missing_assets: {len(gate['assets']['missing_assets'])}",
        f"- asset_presence_rate: {gate['actuals']['asset_presence_rate']:.3f}",
        "",
    ]
    (report_dir / "topology_dod_lab_gate_report.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"run_id": run_id, "gate_pass": gate["pass"], "report_dir": str(report_dir)}, ensure_ascii=False))
    return 0 if gate["pass"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
