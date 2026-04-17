from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sre_agent.config import load_config
from sre_agent.models.ontology import EntityType
from sre_agent.topology import discovery


REPO_ROOT = Path(__file__).resolve().parents[2]
UNIFIED_CONFIG = REPO_ROOT / "sre_agent" / "conf" / "ontology.unified.lab.yaml"


def test_load_unified_inventory_fixture_has_expected_sections() -> None:
    payload = discovery.load_unified_inventory(UNIFIED_CONFIG)

    static_topology = payload["static_topology"]
    dynamic_discovery = payload["dynamic_discovery"]

    assert isinstance(static_topology["clusters"], list) and static_topology["clusters"]
    assert isinstance(static_topology["switches"], list) and static_topology["switches"]
    assert isinstance(static_topology["switch_ports"], list) and static_topology["switch_ports"]
    assert isinstance(dynamic_discovery["node_providers"]["workers"], list)
    assert isinstance(dynamic_discovery["switch_providers"]["switches"], dict)


def test_load_unified_inventory_rejects_unknown_relation_endpoint(tmp_path: Path) -> None:
    invalid_path = tmp_path / "ontology.unified.bad.yaml"
    invalid_path.write_text(
        "\n".join(
            [
                "static_topology:",
                "  clusters: [{id: cluster-a}]",
                "  switches: [{id: sw-a}]",
                "  switch_ports: []",
                "  nodes: [{id: worker-a}]",
                "  gpus: []",
                "  bmc_endpoints: []",
                "  static_relations:",
                "    - source_id: cluster-a",
                "      target_id: sw-missing",
                "      relation: part_of",
                "dynamic_discovery:",
                "  k8s: {cluster_name: aidc-lab, namespaces: []}",
                "  prometheus:",
                "    url: http://prom",
                "    baseline_queries: {cpu_util: a, memory_util: b}",
                "  node_providers:",
                "    workers: [{name: worker-a}]",
                "  switch_providers:",
                "    switches: {sw-a: {host: 10.0.0.1, username: user, password: pass}}",
                "  discovery_policies: {}",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="unknown target_id"):
        discovery.load_unified_inventory(invalid_path)


def test_discover_static_snapshot_uses_unified_cluster_relations(tmp_path: Path) -> None:
    db_path = tmp_path / "ontology.db"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {db_path}",
                "  discovery:",
                f"    unified_inventory_path: {UNIFIED_CONFIG}",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(cfg_path)

    nodes, edges, _counts = asyncio.run(discovery.discover_static_snapshot(config))

    node_ids = {node.id for node in nodes}
    edge_keys = {(edge.source_id, edge.target_id, edge.relation.value) for edge in edges}
    assert "cluster:aidc-lab" in node_ids
    assert "sw-200g" in node_ids
    assert ("cluster:aidc-lab", "sw-200g", "part_of") in edge_keys
    cluster_node = next(node for node in nodes if node.id == "cluster:aidc-lab")
    assert cluster_node.entity_type == EntityType.CLUSTER


def test_discover_live_snapshot_prefers_unified_dynamic_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "ontology.db"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {db_path}",
                "  discovery:",
                f"    unified_inventory_path: {UNIFIED_CONFIG}",
                "    live_inventory_path: /tmp/non-existent-live.yaml",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(cfg_path)

    captured: dict[str, object] = {}

    async def _fake_scan_live_sources(raw_config: dict, **kwargs: object):
        captured["raw_config"] = raw_config
        captured["kwargs"] = kwargs
        return [], [], {"mock": {"nodes": 0, "edges": 0}}

    monkeypatch.setattr(discovery, "scan_live_sources", _fake_scan_live_sources)

    nodes, edges, counts = asyncio.run(discovery.discover_live_snapshot(config))

    assert nodes == []
    assert edges == []
    assert counts == {"mock": {"nodes": 0, "edges": 0}}
    raw_config = captured["raw_config"]
    assert isinstance(raw_config, dict)
    assert "inventory" in raw_config
    assert "switches" in raw_config
    assert "monitor" in raw_config
