from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_CONFIG = REPO_ROOT / "sre_agent" / "conf" / "config.lab.yaml"
STATIC_TOPOLOGY_CONFIG = REPO_ROOT / "fault_injector" / "ontology-static-topology.lab.yaml"
LIVE_INVENTORY_CONFIG = REPO_ROOT / "sre_agent" / "conf" / "live_inventory.lab.yaml"


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(payload, dict), f"{path} must decode to a mapping"
    return payload


def _switch_port_map(payload: dict, switch_name: str) -> dict[str, dict]:
    switches = payload.get("ontology", {}).get("discovery", {}).get("switches", [])
    for switch in switches:
        if switch.get("name") == switch_name:
            ports = switch.get("ports", [])
            return {
                str(port.get("name")): port
                for port in ports
                if isinstance(port, dict) and str(port.get("name", "")).strip()
            }
    raise AssertionError(f"switch {switch_name!r} not found in payload")


def test_lab_cli_config_tracks_fault_injector_inventory() -> None:
    inventory = _load_yaml(LIVE_INVENTORY_CONFIG)
    lab_config = _load_yaml(LAB_CONFIG)

    workers = inventory.get("inventory", {}).get("workers", [])
    switches = inventory.get("switches", {})
    worker_names = {str(item["name"]) for item in workers}

    assert worker_names == {"worker-01", "worker-02", "worker-03", "worker-04", "worker-05", "worker-06"}
    assert "sw-200g" in switches
    assert lab_config["ontology"]["discovery"]["mode"] == "live"
    assert lab_config["ontology"]["discovery"]["live_inventory_path"] == "sre_agent/conf/live_inventory.lab.yaml"
    assert lab_config["ontology"]["discovery"]["k8s_cluster_name"] == "aidc-lab"
    assert lab_config["ontology"]["discovery"]["k8s_namespaces"] == ["default", "sre", "inference"]
    assert lab_config["global"]["prometheus_url"] == inventory["monitor"]["prometheus_url"]

    ports = _switch_port_map(lab_config, "sw-200g")
    assert ports["200GE1/0/1"]["connected_to"] == "worker-01"
    assert ports["200GE1/0/2"]["connected_to"] == "worker-02"
    assert ports["200GE1/0/3"]["connected_to"] == "worker-03"
    assert ports["200GE1/0/4"]["connected_to"] == "worker-04"
    assert ports["200GE1/0/5"]["connected_to"] == "worker-05"
    assert ports["200GE1/0/6"]["connected_to"] == "worker-06"


def test_static_topology_seed_matches_lab_cli_switch_mapping() -> None:
    lab_config = _load_yaml(LAB_CONFIG)
    static_topology = _load_yaml(STATIC_TOPOLOGY_CONFIG)

    static_ports = _switch_port_map(static_topology, "sw-200g")
    lab_ports = _switch_port_map(lab_config, "sw-200g")

    assert static_ports.keys() == lab_ports.keys()
    for port_name in static_ports:
        for field in ("connected_to", "status", "speed_gbps", "source", "mapping_source"):
            assert static_ports[port_name].get(field) == lab_ports[port_name].get(field)
