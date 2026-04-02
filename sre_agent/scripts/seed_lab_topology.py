from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sre_agent.config import load_config
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.graph import OntologyGraph


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed node/BMC/GPU topology into the ontology DB from a lab YAML file.")
    parser.add_argument(
        "--config",
        default="sre_agent/conf/config.lab.test.yaml",
        help="Path to the lab topology config file.",
    )
    return parser.parse_args()


def _load_raw_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"config must decode to a mapping: {path}")
    return payload


def _now() -> datetime:
    return datetime.now(UTC)


def _node_record(raw: dict[str, Any]) -> OntologyNode:
    node_id = str(raw["id"]).strip()
    return OntologyNode(
        id=node_id,
        entity_type=EntityType.NODE,
        name=str(raw.get("name", node_id)).strip() or node_id,
        properties={
            "role": raw.get("role", "unknown"),
            "source": "lab_seed",
            "switch": raw.get("switch"),
            "port": raw.get("port"),
        },
        status="online",
        updated_at=_now(),
    )


def _bmc_record(raw: dict[str, Any]) -> OntologyNode:
    node_id = str(raw["id"]).strip()
    bmc_ip = str(raw.get("bmc_ip", "")).strip()
    if not bmc_ip:
        raise ValueError(f"node {node_id} is missing bmc_ip")
    return OntologyNode(
        id=f"bmc:{node_id}",
        entity_type=EntityType.BMC_ENDPOINT,
        name=f"BMC {node_id}",
        properties={"ip": bmc_ip, "source": "lab_seed"},
        status="online",
        updated_at=_now(),
    )


def _gpu_records(raw: dict[str, Any]) -> list[OntologyNode]:
    node_id = str(raw["id"]).strip()
    gpu_ids = raw.get("gpu_ids", [])
    if not isinstance(gpu_ids, list):
        raise ValueError(f"node {node_id} has non-list gpu_ids")
    nodes: list[OntologyNode] = []
    for gpu_id in gpu_ids:
        value = str(gpu_id).strip()
        if not value:
            continue
        nodes.append(
            OntologyNode(
                id=value,
                entity_type=EntityType.GPU,
                name=value,
                properties={"host": node_id, "source": "lab_seed"},
                status="online",
                updated_at=_now(),
            )
        )
    return nodes


def _edges_for_node(raw: dict[str, Any]) -> list[OntologyEdge]:
    node_id = str(raw["id"]).strip()
    edges = [
        OntologyEdge(
            source_id=f"bmc:{node_id}",
            target_id=node_id,
            relation=RelationType.MANAGES,
            properties={},
        )
    ]
    for gpu_id in raw.get("gpu_ids", []):
        value = str(gpu_id).strip()
        if not value:
            continue
        edges.append(
            OntologyEdge(
                source_id=value,
                target_id=node_id,
                relation=RelationType.PART_OF,
                properties={},
            )
        )
    return edges


async def _seed(config_path: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    raw = _load_raw_yaml(config_path)
    seed_nodes = raw.get("lab_seed", {}).get("nodes", [])
    if not isinstance(seed_nodes, list) or not seed_nodes:
        raise ValueError(f"No lab_seed.nodes entries found in {config_path}")

    graph = OntologyGraph(cfg.ontology.db_path)
    await graph.connect()
    try:
        nodes: list[OntologyNode] = []
        edges: list[OntologyEdge] = []
        for entry in seed_nodes:
            if not isinstance(entry, dict):
                continue
            nodes.append(_node_record(entry))
            nodes.append(_bmc_record(entry))
            nodes.extend(_gpu_records(entry))
            edges.extend(_edges_for_node(entry))

        await graph.add_nodes(nodes)
        await graph.add_edges(edges)
        return graph.summarize()
    finally:
        await graph.close()


def main() -> int:
    args = _parse_args()
    summary = asyncio.run(_seed(Path(args.config)))
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
