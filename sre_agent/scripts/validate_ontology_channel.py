from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.ontology import OntologyChannel
from lib.tests._real_backends import OntologyHttpAdapter


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the ontology HTTP channel against a live app and seeded graph."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("SRE_ONTOLOGY_URL", "http://127.0.0.1:8001"),
        help="Ontology app base URL.",
    )
    parser.add_argument(
        "--query-path",
        default=os.getenv("SRE_ONTOLOGY_QUERY_PATH", "/api/ontology/query"),
        help="Ontology query API path.",
    )
    parser.add_argument(
        "--blast-path",
        default=os.getenv("SRE_ONTOLOGY_BLAST_PATH", "/api/ontology/blast"),
        help="Ontology blast-radius API path.",
    )
    parser.add_argument(
        "--path-path",
        default=os.getenv("SRE_ONTOLOGY_PATH_PATH", "/api/ontology/path"),
        help="Ontology path API path.",
    )
    parser.add_argument(
        "--refresh-path",
        default=os.getenv("SRE_ONTOLOGY_REFRESH_PATH", "/api/ontology/refresh"),
        help="Ontology refresh API path.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("SRE_ONTOLOGY_TOKEN", ""),
        help="Bearer token for the ontology API.",
    )
    parser.add_argument(
        "--entity-type",
        default="node",
        help="Entity type used for the ontology query validation.",
    )
    parser.add_argument(
        "--query-source",
        default="lab_seed",
        help="Value used for the query source filter.",
    )
    parser.add_argument(
        "--blast-entity-id",
        default="gpu:worker-01:0",
        help="Entity ID used for the blast-radius validation.",
    )
    parser.add_argument(
        "--from-id",
        default="sw-200g-a:200GE1/0/1",
        help="Source node ID used for the path validation.",
    )
    parser.add_argument(
        "--to-id",
        default="worker-01",
        help="Destination node ID used for the path validation.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("SRE_HTTP_TIMEOUT_SEC", "15")),
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=max(1, int(os.getenv("SRE_HTTP_RETRY_COUNT", "2"))),
        help="HTTP retry count.",
    )
    return parser.parse_args()


def _headers(token: str) -> dict[str, str]:
    value = token.strip()
    if not value:
        raise ValueError("missing ontology bearer token; pass --token or set SRE_ONTOLOGY_TOKEN")
    return {"Authorization": f"Bearer {value}"}


async def _validate(args: argparse.Namespace) -> None:
    adapter = OntologyHttpAdapter(
        base_url=args.base_url,
        query_path=args.query_path,
        blast_path=args.blast_path,
        path_path=args.path_path,
        refresh_path=args.refresh_path or None,
        timeout=args.timeout,
        retries=args.retries,
        headers=_headers(args.token),
    )
    channel = OntologyChannel(ontology=adapter)

    await channel.connect()
    try:
        rows = await channel.query(args.entity_type, {"source": args.query_source})
        if not rows:
            raise RuntimeError(
                f"ontology query returned no rows for entity_type={args.entity_type} source={args.query_source}"
            )

        blast = await channel.get_blast_radius(args.blast_entity_id)
        affected_count = int(blast.get("affected_count", 0) or 0)
        if not blast:
            raise RuntimeError(f"ontology blast radius returned an empty payload for {args.blast_entity_id}")
        if affected_count <= 0:
            raise RuntimeError(
                f"ontology blast radius returned affected_count={affected_count} for {args.blast_entity_id}"
            )

        path = await channel.get_path(args.from_id, args.to_id)
        if not path:
            raise RuntimeError(f"ontology path returned no path for from_id={args.from_id} to_id={args.to_id}")

        print("validation_status: ok")
        print(f"query_count: {len(rows)}")
        print(f"first_query_id: {rows[0].get('id', '')}")
        print(f"blast_entity_id: {args.blast_entity_id}")
        print(f"blast_affected_count: {affected_count}")
        print("path: " + " -> ".join(str(item) for item in path))
    finally:
        await channel.disconnect()
        await adapter.aclose()


def main() -> int:
    args = _parse_args()
    asyncio.run(_validate(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
