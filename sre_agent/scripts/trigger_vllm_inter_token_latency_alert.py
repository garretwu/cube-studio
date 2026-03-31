#!/usr/bin/env python3
"""Trigger the VLLMInterTokenLatencyP95High alert and capture the alert payload."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.alert import AlertChannel
from lib.tests._real_backends import PrometheusHttpBackend
from sre_agent.models.alert import Alert

DEFAULT_DEMO_CONFIG = Path("fault_injector/vllm-latency-p95-live-demo.yaml")
DEFAULT_DEMO_KEY = "vllm_inter_token_latency_p95_alert_remediation"
DEFAULT_OUTPUT = Path("data/demo/vllm-inter-token-latency-alert-capture.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trigger VLLMInterTokenLatencyP95High and capture the alert payload.")
    parser.add_argument("--demo-config", default=str(DEFAULT_DEMO_CONFIG), help="Demo YAML path.")
    parser.add_argument("--demo-key", default=DEFAULT_DEMO_KEY, help="Demo key under demo config.")
    parser.add_argument("--scenario", default="gpu_contention", help="Fault injector scenario name.")
    parser.add_argument("--poll-seconds", type=int, default=15, help="Alert polling interval in seconds.")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="Overall timeout waiting for the alert.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Capture output path.")
    parser.add_argument("--output-format", choices=["json", "txt"], default="json", help="Capture output format.")
    return parser


def load_yaml(path: str) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"Config {path} must contain a YAML object")
    return raw


def load_demo_settings(raw: dict[str, Any], demo_key: str, path: str) -> dict[str, Any]:
    demos = raw.get("demo")
    if not isinstance(demos, dict):
        raise SystemExit(f"Config {path} is missing top-level demo section")
    demo = demos.get(demo_key)
    if not isinstance(demo, dict):
        raise SystemExit(f"Config {path} is missing demo.{demo_key}")
    return demo


def load_prometheus_url(raw_cfg: dict[str, Any]) -> str:
    monitor = raw_cfg.get("monitor")
    if not isinstance(monitor, dict):
        return ""
    return str(monitor.get("prometheus_url") or "").strip()


def normalize_alert_payload(alert: Alert) -> dict[str, Any]:
    return {
        "alert_name": alert.alert_name,
        "status": alert.status.value,
        "severity": alert.severity.value,
        "summary": alert.summary,
        "description": alert.description,
        "labels": alert.labels,
        "annotations": alert.annotations,
        "starts_at": alert.starts_at.isoformat(),
        "ends_at": alert.ends_at.isoformat() if alert.ends_at else None,
        "fingerprint": alert.fingerprint,
        "source": alert.source,
    }


def render_text_output(payload: dict[str, Any]) -> str:
    lines = [
        "VLLMInterTokenLatencyP95High Alert Capture",
        f"Alert Name: {payload.get('alert_name')}",
        f"Prometheus URL: {payload.get('prometheus_url')}",
        f"Scenario: {payload.get('scenario')}",
        f"Detected: {payload.get('detected')}",
        f"Detected At: {payload.get('detected_at')}",
        f"Poll Count: {payload.get('poll_count')}",
        f"Current Alert Count: {payload.get('current_alert_count')}",
        f"History Alert Count: {payload.get('history_alert_count')}",
        "",
        "Captured Alert:",
        json.dumps(payload.get("alert"), ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines) + "\n"


def maybe_write_output(path_text: str, output_format: str, payload: dict[str, Any]) -> None:
    target = Path(path_text).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    target.write_text(render_text_output(payload), encoding="utf-8")


async def start_process(*cmd: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def stop_process(proc: asyncio.subprocess.Process | None, *, name: str) -> None:
    if proc is None or proc.returncode is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(proc.wait(), timeout=20)
        return
    except asyncio.TimeoutError:
        pass
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
        return
    except asyncio.TimeoutError:
        pass
    proc.kill()
    await proc.wait()


async def poll_for_alert(
    *,
    prometheus_url: str,
    alert_name: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> tuple[Alert | None, list[Alert], list[Alert], int]:
    backend = PrometheusHttpBackend(base_url=prometheus_url, timeout=30.0, retries=3)
    channel = AlertChannel(alertmanager_url="", prometheus_url=prometheus_url, metrics_backend=backend)
    current_alerts: list[Alert] = []
    history_alerts: list[Alert] = []
    try:
        await channel.connect()
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        poll_count = 0
        while True:
            poll_count += 1
            current_alerts = await channel.get_alerts(filter_labels={"alertname": alert_name})
            history_alerts = await channel.get_alert_history(alert_name, lookback="6h")
            if current_alerts:
                selected = max(current_alerts, key=lambda item: item.starts_at)
                return selected, current_alerts, history_alerts, poll_count
            if asyncio.get_running_loop().time() >= deadline:
                return None, current_alerts, history_alerts, poll_count
            await asyncio.sleep(poll_seconds)
    finally:
        await channel.disconnect()
        await backend.aclose()


async def main_async(args: argparse.Namespace) -> int:
    raw_cfg = load_yaml(args.demo_config)
    demo = load_demo_settings(raw_cfg, args.demo_key, args.demo_config)
    alert_name = str(demo.get("alert_name") or "VLLMInterTokenLatencyP95High").strip()
    load_cfg = str(demo.get("load_simulator_config") or "").strip()
    if not load_cfg:
        raise SystemExit("demo.load_simulator_config is required")
    prometheus_url = load_prometheus_url(raw_cfg)
    if not prometheus_url:
        raise SystemExit("monitor.prometheus_url is required")

    python_bin = Path("/root/workspace/.cube/bin/python")
    env = {"PYTHONPATH": str(REPO_ROOT)}
    load_proc: asyncio.subprocess.Process | None = None
    inject_proc: asyncio.subprocess.Process | None = None

    try:
        print("Starting load simulator...")
        load_proc = await start_process(
            str(python_bin),
            "-m",
            "load_simulator",
            "run",
            "--config",
            load_cfg,
            "--only",
            "inference",
            "--output-format",
            "json",
        )

        warmup = int(demo.get("load_warmup_seconds") or 30)
        print(f"Waiting for load warmup ({warmup}s)...")
        await asyncio.sleep(warmup)

        print("Starting fault injection...")
        inject_proc = await start_process(
            str(python_bin),
            "-m",
            "fault_injector.cli",
            "run",
            "--config",
            args.demo_config,
            "--scenario",
            args.scenario,
            "--no-monitor",
            "--yes",
        )

        print(f"Polling Prometheus for alert {alert_name} ...")
        selected_alert, current_alerts, history_alerts, poll_count = await poll_for_alert(
            prometheus_url=prometheus_url,
            alert_name=alert_name,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        )

        payload: dict[str, Any] = {
            "alert_name": alert_name,
            "prometheus_url": prometheus_url,
            "scenario": args.scenario,
            "detected": selected_alert is not None,
            "detected_at": datetime.now(UTC).isoformat(),
            "poll_count": poll_count,
            "current_alert_count": len(current_alerts),
            "history_alert_count": len(history_alerts),
            "alert": normalize_alert_payload(selected_alert) if selected_alert is not None else None,
        }
        maybe_write_output(args.output, args.output_format, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if selected_alert is not None else 1
    finally:
        await stop_process(inject_proc, name="fault_injector")
        await stop_process(load_proc, name="load_simulator")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
