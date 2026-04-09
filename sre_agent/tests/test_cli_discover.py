from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner

import sre_agent.cli as cli_module
from sre_agent.cli import main
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.graph import OntologyGraph


def test_discover_then_topology_cli_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "ontology.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {db_path}",
                "  discovery:",
                "    switches:",
                "      - name: sw-200g",
                "        type: H3C-S9855",
                "        ports:",
                "          - name: HundredGigE1/0/1",
                "            connected_to: node-a",
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()

    discover = runner.invoke(main, ["discover", "--config", str(config_path)])
    assert discover.exit_code == 0
    assert "Discovery Complete" in discover.output
    assert "Source Mode: static" in discover.output
    assert "Nodes: 2" in discover.output
    assert "Edges: 2" in discover.output

    topology = runner.invoke(main, ["topology", "--config", str(config_path)])
    assert topology.exit_code == 0
    assert "Topology Summary" in topology.output
    assert "AIDC: test-aidc" in topology.output
    assert "Nodes: 2" in topology.output
    assert "Edges: 2" in topology.output
    assert "- switch: 1" in topology.output
    assert "- switch_port: 1" in topology.output


def test_discover_live_mode_requires_refresh_only(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {tmp_path / 'ontology.db'}",
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["discover", "--mode", "live", "--config", str(config_path)])

    assert result.exit_code != 0
    assert "requires --refresh-only" in result.output


def test_discover_ingests_lab_seed_nodes_in_single_command(tmp_path: Path) -> None:
    db_path = tmp_path / "ontology.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {db_path}",
                "  discovery:",
                "    switches:",
                "      - name: sw-200g-a",
                "        type: H3C-S9855",
                "        source: real-test",
                "        ports:",
                "          - name: HundredGigE1/0/1",
                "            connected_to: worker-01",
                "lab_seed:",
                "  nodes:",
                "    - id: worker-01",
                "      role: compute",
                "      bmc_ip: 10.0.0.10",
                "      switch: sw-200g-a",
                "      port: HundredGigE1/0/1",
                "      gpu_ids:",
                "        - gpu:worker-01:0",
                "        - gpu:worker-01:1",
                "    - id: node-02",
                "      role: infra",
                "      bmc_ip: 10.0.0.20",
                "      switch: sw-200g-a",
                "      port: HundredGigE1/0/2",
                "      gpu_ids: []",
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    discover = runner.invoke(main, ["discover", "--config", str(config_path), "--refresh-only"])

    assert discover.exit_code == 0
    assert "Discovery Complete" in discover.output
    assert "Nodes: 8" in discover.output
    assert "Edges: 6" in discover.output

    async def _assert_graph() -> None:
        graph = OntologyGraph(db_path=str(db_path))
        await graph.connect()
        try:
            summary = graph.summarize()
            assert summary["entity_type_counts"] == {
                "bmc_endpoint": 2,
                "gpu": 2,
                "node": 2,
                "switch": 1,
                "switch_port": 1,
            }

            worker = graph.get_entity("worker-01")
            assert worker is not None
            assert worker.properties["role"] == "compute"
            assert worker.properties["switch"] == "sw-200g-a"

            switch = graph.get_entity("sw-200g-a")
            assert switch is not None
            assert switch.properties["source"] == "real-test"

            edge_keys = {(edge.source_id, edge.target_id, edge.relation.value) for edge in graph.list_edges()}
            assert ("bmc:worker-01", "worker-01", "manages") in edge_keys
            assert ("gpu:worker-01:0", "worker-01", "part_of") in edge_keys
            assert ("sw-200g-a:HundredGigE1/0/1", "worker-01", "connected_to") in edge_keys
        finally:
            await graph.close()

    asyncio.run(_assert_graph())


def test_discover_live_mode_validates_inventory_schema(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ontology.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {db_path}",
                "  discovery:",
                "    k8s_cluster_name: aidc-lab",
                "    k8s_namespaces: []",
            ]
        ),
        encoding="utf-8",
    )
    inventory_path = tmp_path / "fault-injector-test.yaml"
    inventory_path.write_text(
        "\n".join(
            [
                "inventory:",
                "  workers:",
                "    - name: worker-01",
                "      redfish:",
                "        bmc_host: 10.0.0.10",
                "        username: admin",
                "        password: pass",
                "monitor:",
                "  prometheus_url: http://prom",
                "  baseline_queries:",
                "    cpu_util: avg(cpu)",
                "switches:",
                "  sw-1:",
                "    host: 10.0.0.2",
                "    username: api",
                "    password: pass",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_LIVE_INVENTORY_PATH", inventory_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["discover", "--mode", "live", "--refresh-only", "--config", str(config_path)],
    )
    assert result.exit_code != 0
    assert "monitor.baseline_queries.memory_util is required" in result.output


def test_discover_live_mode_persists_mocked_scanner_aggregation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "ontology.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {db_path}",
                "  discovery:",
                "    k8s_cluster_name: aidc-lab",
                "    k8s_namespaces: []",
            ]
        ),
        encoding="utf-8",
    )
    inventory_path = tmp_path / "fault-injector-test.yaml"
    inventory_path.write_text(
        "\n".join(
            [
                "inventory:",
                "  workers:",
                "    - name: worker-01",
                "      redfish:",
                "        bmc_host: 10.0.0.10",
                "        username: admin",
                "        password: pass",
                "monitor:",
                "  prometheus_url: http://prom",
                "  baseline_queries:",
                "    cpu_util: avg(cpu)",
                "    memory_util: avg(mem)",
                "switches:",
                "  sw-1:",
                "    host: 10.0.0.2",
                "    username: api",
                "    password: pass",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_LIVE_INVENTORY_PATH", inventory_path)

    live_node = OntologyNode(
        id="worker-01",
        entity_type=EntityType.NODE,
        name="worker-01",
        properties={"source": "live_inventory"},
    )
    live_bmc = OntologyNode(
        id="bmc:worker-01",
        entity_type=EntityType.BMC_ENDPOINT,
        name="BMC worker-01",
        properties={"ip": "10.0.0.10"},
    )
    live_edge = OntologyEdge(
        source_id="bmc:worker-01",
        target_id="worker-01",
        relation=RelationType.MANAGES,
        properties={},
    )

    async def _fake_scan_live_sources(config, inventory: dict[str, object]):
        assert list(config.ontology.discovery.k8s_namespaces) == []
        assert str(config.ontology.discovery.k8s_cluster_name) == "aidc-lab"
        assert inventory["inventory"]["workers"][0]["name"] == "worker-01"
        return (
            [live_node, live_bmc],
            [live_edge],
            {
                "bmc": {"nodes": 1, "edges": 1},
                "k8s": {"nodes": 0, "edges": 0},
                "prometheus": {"nodes": 0, "edges": 0},
                "switch": {"nodes": 0, "edges": 0},
            },
        )

    monkeypatch.setattr(cli_module, "_scan_live_sources", _fake_scan_live_sources)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["discover", "--mode", "live", "--refresh-only", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "Source Mode: live" in result.output
    assert "Nodes: 2" in result.output
    assert "Edges: 1" in result.output
    assert "Scanner Counts:" in result.output
    assert "- bmc: nodes=1, edges=1" in result.output

    async def _assert_live_graph() -> None:
        graph = OntologyGraph(db_path=str(db_path))
        await graph.connect()
        try:
            summary = graph.summarize()
            assert summary["entity_type_counts"] == {"bmc_endpoint": 1, "node": 1}
            assert graph.get_path("bmc:worker-01", "worker-01") == ["bmc:worker-01", "worker-01"]
        finally:
            await graph.close()

    asyncio.run(_assert_live_graph())
