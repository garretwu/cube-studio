from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from sre_agent.cli import main


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


def test_discover_live_mode_is_explicitly_not_wired(tmp_path: Path) -> None:
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
    assert "live discovery is not wired yet" in result.output
