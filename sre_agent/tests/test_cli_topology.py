from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from sre_agent.cli import main
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType


def test_topology_cli_reads_config_and_prints_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "ontology.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {db_path}",
            ]
        ),
        encoding="utf-8",
    )

    import asyncio

    async def _seed() -> None:
        graph = OntologyGraph(db_path=str(db_path))
        await graph.connect()
        await graph.add_node(OntologyNode(id="node-a", entity_type=EntityType.NODE, name="node-a"))
        await graph.add_node(
            OntologyNode(id="svc-a", entity_type=EntityType.INFERENCE_SERVICE, name="svc-a")
        )
        await graph.add_edge(
            OntologyEdge(source_id="svc-a", target_id="node-a", relation=RelationType.HOSTED_ON)
        )
        await graph.close()

    asyncio.run(_seed())

    runner = CliRunner()
    result = runner.invoke(main, ["topology", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Topology Summary" in result.output
    assert "AIDC: test-aidc" in result.output
    assert "Nodes: 2" in result.output
    assert "Edges: 1" in result.output
    assert "- node: 1" in result.output
    assert "- inference_service: 1" in result.output

