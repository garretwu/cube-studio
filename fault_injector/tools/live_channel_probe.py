from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

# Ensure repository root is importable when run as a script file.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.ipmi import IPMIChannel
from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel
from lib.channels.redfish import RedfishChannel
from lib.channels.switch import SwitchChannel


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only live probe for BMC, Redfish, K8s, Prometheus, and Switch channels.")
    parser.add_argument(
        "--config",
        default="fault_injector/fault-injector-test.yaml",
        help="Path to the fault injector YAML config.",
    )
    parser.add_argument(
        "--worker",
        default="worker-01",
        help="Worker name under inventory.workers to use for BMC/IPMI probing.",
    )
    parser.add_argument(
        "--switch",
        action="append",
        dest="switches",
        default=None,
        help="Specific switch name to probe. Repeat to probe multiple switches. Default: all configured switches.",
    )
    parser.add_argument("--skip-ipmi", action="store_true", help="Skip IPMI probing.")
    parser.add_argument("--skip-redfish", action="store_true", help="Skip Redfish probing.")
    parser.add_argument("--skip-k8s", action="store_true", help="Skip Kubernetes probing.")
    parser.add_argument("--skip-prometheus", action="store_true", help="Skip Prometheus probing.")
    parser.add_argument("--skip-switch", action="store_true", help="Skip switch probing.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    return parser.parse_args()


def _load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"config file does not exist: {config_path}")
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"config must decode to a mapping: {config_path}")
    return payload


def _find_worker(config: dict[str, Any], worker_name: str) -> dict[str, Any]:
    workers = config.get("inventory", {}).get("workers", [])
    for worker in workers:
        if worker.get("name") == worker_name:
            return worker
    available = [worker.get("name") for worker in workers]
    raise ValueError(f"worker {worker_name!r} not found; available workers: {available}")


def _classify_error(error: str) -> str:
    lowered = error.lower()
    if "pyghmi is not installed" in lowered:
        return "dependency_missing"
    if any(token in lowered for token in ("authentication", "unauthorized", "forbidden", "login failed", "credential")):
        return "auth_failed"
    if any(token in lowered for token in ("timeout", "refused", "unreachable", "no route", "connection reset", "connect failed")):
        return "connection_failed"
    return "api_failed"


def _parse_json_output(output: str) -> Any:
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def _ok_result(channel: str, target: str, latency_ms: int, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": channel,
        "target": target,
        "success": True,
        "status": "ok",
        "latency_ms": latency_ms,
        "error": "",
        "details": details,
    }


def _error_result(channel: str, target: str, latency_ms: int, error: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "channel": channel,
        "target": target,
        "success": False,
        "status": _classify_error(error),
        "latency_ms": latency_ms,
        "error": error,
        "details": details or {},
    }


async def _probe_ipmi(worker_name: str, ipmi_cfg: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    channel = IPMIChannel(timeout=int(ipmi_cfg.get("timeout", 30)))
    connect = await channel.connect(**ipmi_cfg)
    if not connect.success:
        return _error_result("ipmi", worker_name, int((time.monotonic() - start) * 1000), connect.error)
    mc_info = await channel.get_mc_info(**ipmi_cfg)
    if not mc_info.success:
        return _error_result("ipmi", worker_name, int((time.monotonic() - start) * 1000), mc_info.error)
    details = _parse_json_output(mc_info.output)
    return _ok_result("ipmi", worker_name, int((time.monotonic() - start) * 1000), {"mc_info": details})


async def _probe_redfish(worker_name: str, redfish_cfg: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    channel = RedfishChannel(timeout=int(redfish_cfg.get("timeout", 30)))
    try:
        auth = await channel.authenticate(
            redfish_cfg["bmc_host"],
            redfish_cfg["username"],
            redfish_cfg["password"],
            verify_tls=bool(redfish_cfg.get("verify_tls", True)),
        )
        if not auth.success:
            return _error_result("redfish", worker_name, int((time.monotonic() - start) * 1000), auth.error)

        info = await channel.get_bmc_info(
            redfish_cfg["bmc_host"],
            verify_tls=bool(redfish_cfg.get("verify_tls", True)),
        )
        if not info.success:
            return _error_result("redfish", worker_name, int((time.monotonic() - start) * 1000), info.error)

        raw = _parse_json_output(info.output) or {}
        details = {
            "manager": raw.get("manager", {}),
            "system": raw.get("system", {}),
            "chassis": raw.get("chassis", {}),
            "service_root": raw.get("service_root", {}),
        }
        return _ok_result("redfish", worker_name, int((time.monotonic() - start) * 1000), details)
    finally:
        await channel.close()


async def _probe_k8s() -> dict[str, Any]:
    start = time.monotonic()
    channel = K8sChannel(kubeconfig="~/.kube/config")
    namespace = "default"
    result = await channel.execute("get_pods", {"namespace": namespace})
    if not result.success:
        return _error_result("k8s", namespace, int((time.monotonic() - start) * 1000), result.error)
    pod_names = [line.strip() for line in result.output.splitlines() if line.strip()]
    return _ok_result(
        "k8s",
        namespace,
        int((time.monotonic() - start) * 1000),
        {"namespace": namespace, "pod_count": len(pod_names), "pods": pod_names},
    )


async def _probe_prometheus(monitor_cfg: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    base_url = str(monitor_cfg.get("prometheus_url", "")).strip()
    if not base_url:
        return _error_result("prometheus", "monitor.prometheus_url", 0, "Prometheus URL is not configured")

    channel = PrometheusChannel(base_url=base_url, timeout=15)
    try:
        queries = monitor_cfg.get("baseline_queries") or {}
        if not isinstance(queries, dict):
            return _error_result("prometheus", base_url, int((time.monotonic() - start) * 1000), "baseline_queries must be a mapping")

        ordered_queries: list[tuple[str, str]] = []
        cpu_query = queries.get("cpu_util")
        if isinstance(cpu_query, str) and cpu_query.strip():
            ordered_queries.append(("cpu_util", cpu_query))
        for name, promql in queries.items():
            if name == "cpu_util":
                continue
            if isinstance(promql, str) and promql.strip():
                ordered_queries.append((str(name), promql))

        if not ordered_queries:
            return _error_result("prometheus", base_url, int((time.monotonic() - start) * 1000), "No baseline Prometheus queries configured")

        values: dict[str, dict[str, Any]] = {}
        for name, promql in ordered_queries:
            try:
                values[name] = {"query": promql, "value": await channel.query_instant(promql)}
            except Exception as exc:  # pragma: no cover - real environment branch
                return _error_result(
                    "prometheus",
                    base_url,
                    int((time.monotonic() - start) * 1000),
                    str(exc),
                    details={"failed_query": name, "query": promql},
                )
        return _ok_result("prometheus", base_url, int((time.monotonic() - start) * 1000), {"queries": values})
    finally:
        await channel.close()


async def _probe_switch(switch_name: str, switches_cfg: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    channel = SwitchChannel(devices=switches_cfg, timeout=int(switches_cfg[switch_name].get("timeout", 30)))
    try:
        if not channel.test_connection(switch_name):
            return _error_result("switch", switch_name, int((time.monotonic() - start) * 1000), "Switch connection test failed")
        result = await channel.execute("get_all_interfaces", {"switch": switch_name})
        if not result.success:
            return _error_result("switch", switch_name, int((time.monotonic() - start) * 1000), result.error)
        interfaces = [line.strip() for line in result.output.splitlines() if line.strip()]
        return _ok_result(
            "switch",
            switch_name,
            int((time.monotonic() - start) * 1000),
            {
                "interface_count": len(interfaces),
                "interfaces_preview": interfaces[:10],
            },
        )
    finally:
        channel.close()


async def _run_probe(args: argparse.Namespace) -> dict[str, Any]:
    config = _load_config(args.config)
    worker = _find_worker(config, args.worker)
    switches_cfg = config.get("switches") or {}
    selected_switches = args.switches or sorted(switches_cfg.keys())

    report: dict[str, Any] = {
        "config": str(Path(args.config)),
        "worker": args.worker,
        "results": [],
    }

    if not args.skip_ipmi:
        report["results"].append(await _probe_ipmi(args.worker, worker["ipmi"]))
    if not args.skip_redfish:
        report["results"].append(await _probe_redfish(args.worker, worker["redfish"]))
    if not args.skip_k8s:
        report["results"].append(await _probe_k8s())
    if not args.skip_prometheus:
        report["results"].append(await _probe_prometheus(config.get("monitor") or {}))
    if not args.skip_switch:
        for switch_name in selected_switches:
            if switch_name not in switches_cfg:
                report["results"].append(
                    _error_result("switch", switch_name, 0, f"switch {switch_name!r} not found in config")
                )
                continue
            report["results"].append(await _probe_switch(switch_name, switches_cfg))

    report["all_success"] = all(item["success"] for item in report["results"])
    return report


def _print_summary(report: dict[str, Any]) -> None:
    print("Live Channel Probe")
    print(f"Config: {report['config']}")
    print(f"Worker: {report['worker']}")
    for item in report["results"]:
        state = "OK" if item["success"] else "FAIL"
        print(
            f"- {item['channel']}[{item['target']}] {state} "
            f"status={item['status']} latency_ms={item['latency_ms']}"
        )
        if item["error"]:
            print(f"  error: {item['error']}")


def main() -> int:
    args = _parse_args()
    report = asyncio.run(_run_probe(args))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
    return 0 if report["all_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
