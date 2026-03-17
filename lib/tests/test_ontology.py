from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any

import pytest

from lib.channels.ontology import OntologyChannel
from lib.tests._real_backends import OntologyHttpAdapter
from lib.tests._real_test_support import (
    allow_write_ops,
    build_auth_headers,
    get_http_retry_count,
    get_http_timeout_sec,
    require_env,
    require_real_tests,
)


class _FakeOntology:
    def __init__(self) -> None:
        self.query_calls: list[tuple[str, dict[str, Any]]] = []
        self.blast_radius_calls: list[str] = []
        self.path_calls: list[tuple[str, str]] = []
        self.refresh_calls: list[str] = []

    def find_entities(self, entity_type: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.query_calls.append((entity_type, filters or {}))
        return [
            {"id": "node-1", "entity_type": entity_type, "status": "healthy"},
            {"id": "node-2", "entity_type": entity_type, "status": "degraded"},
        ]

    async def get_blast_radius(self, entity_id: str) -> dict[str, Any]:
        self.blast_radius_calls.append(entity_id)
        return {"root": entity_id, "affected": {"service:vllm": {"hops": 1}}}

    def get_path(self, from_id: str, to_id: str) -> list[str]:
        self.path_calls.append((from_id, to_id))
        return [from_id, "switch:sw-1", to_id]

    def refresh_entity(self, entity_id: str) -> dict[str, Any]:
        self.refresh_calls.append(entity_id)
        return {"entity_id": entity_id, "refreshed": True}


class _FakeOntologyNoRefresh:
    def find_entities(self, entity_type: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        _ = filters
        return [{"id": "node-3", "entity_type": entity_type}]

    def get_blast_radius(self, entity_id: str) -> dict[str, Any]:
        return {"root": entity_id, "affected": {}}

    def get_path(self, from_id: str, to_id: str) -> list[str]:
        return [from_id, to_id]


class TestOntologyChannelUnit(unittest.IsolatedAsyncioTestCase):
    async def test_unit_connect_disconnect_health(self) -> None:
        channel = OntologyChannel(ontology=_FakeOntology())
        self.assertTrue(await channel.connect())
        health = await channel.health_check()
        self.assertTrue(health["connected"])
        self.assertTrue(health["ontology"])
        self.assertTrue(health["queryable"])
        self.assertTrue(await channel.disconnect())

    async def test_unit_unknown_action(self) -> None:
        channel = OntologyChannel(ontology=_FakeOntology())
        result = await channel.execute("unknown_action", {})
        self.assertFalse(result.success)
        self.assertIn("unknown action", result.error)

    async def test_unit_refresh_fallback_when_not_supported(self) -> None:
        channel = OntologyChannel(ontology=_FakeOntologyNoRefresh())
        await channel.connect()
        result = await channel.refresh_entity("node-3")
        self.assertFalse(result["refreshed"])
        self.assertIn("not implemented", result["reason"])


class TestOntologyChannelIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_integration_query_passthrough(self) -> None:
        ontology = _FakeOntology()
        channel = OntologyChannel(ontology=ontology)
        await channel.connect()

        rows = await channel.query("node", {"rack": "rack-a"})
        self.assertEqual(len(rows), 2)
        self.assertEqual(ontology.query_calls[0], ("node", {"rack": "rack-a"}))

    async def test_integration_get_blast_radius(self) -> None:
        ontology = _FakeOntology()
        channel = OntologyChannel(ontology=ontology)
        await channel.connect()

        blast = await channel.get_blast_radius("node-1")
        self.assertEqual(blast["root"], "node-1")
        self.assertEqual(ontology.blast_radius_calls[0], "node-1")

    async def test_integration_get_path(self) -> None:
        ontology = _FakeOntology()
        channel = OntologyChannel(ontology=ontology)
        await channel.connect()

        path = await channel.get_path("gpu:0", "service:vllm")
        self.assertEqual(path, ["gpu:0", "switch:sw-1", "service:vllm"])
        self.assertEqual(ontology.path_calls[0], ("gpu:0", "service:vllm"))


class TestOntologyChannelE2E(unittest.IsolatedAsyncioTestCase):
    async def test_e2e_mocked_ontology_flow(self) -> None:
        ontology = _FakeOntology()
        channel = OntologyChannel(ontology=ontology)

        await channel.connect()
        entities = await channel.query("node", {"status": "degraded"})
        blast = await channel.get_blast_radius(entities[0]["id"])
        path = await channel.get_path("node:node-1", "service:vllm")
        refreshed = await channel.refresh_entity("node-1")
        health = await channel.health_check()
        await channel.disconnect()

        self.assertTrue(len(entities) >= 1)
        self.assertIn("affected", blast)
        self.assertIsInstance(path, list)
        self.assertTrue(refreshed["refreshed"])
        self.assertTrue(health["connected"])


if __name__ == "__main__":
    unittest.main()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.real
class TestOntologyChannelReal:
    def test_real_ontology_readonly_flow(self) -> None:
        require_real_tests()
        env = require_env(
            "SRE_ONTOLOGY_URL",
            "SRE_ONTOLOGY_QUERY_PATH",
            "SRE_ONTOLOGY_BLAST_PATH",
            "SRE_ONTOLOGY_PATH_PATH",
            "SRE_TEST_ENTITY_TYPE",
            "SRE_TEST_FROM_ID",
            "SRE_TEST_TO_ID",
        )
        timeout_sec = get_http_timeout_sec()
        retry_count = get_http_retry_count()

        adapter = OntologyHttpAdapter(
            base_url=env["SRE_ONTOLOGY_URL"],
            query_path=env["SRE_ONTOLOGY_QUERY_PATH"],
            blast_path=env["SRE_ONTOLOGY_BLAST_PATH"],
            path_path=env["SRE_ONTOLOGY_PATH_PATH"],
            refresh_path=os.getenv("SRE_ONTOLOGY_REFRESH_PATH", "").strip() or None,
            timeout=timeout_sec,
            retries=retry_count,
            headers=build_auth_headers("ONTOLOGY"),
        )
        channel = OntologyChannel(ontology=adapter)

        try:
            assert _run(channel.connect()) is True
            health = _run(channel.health_check())
            assert health.get("connected") is True
            assert health.get("ontology") is True
            assert health.get("queryable") is True

            rows = _run(channel.query(env["SRE_TEST_ENTITY_TYPE"], {"source": "real-test"}))
            assert len(rows) > 0, "ontology query returned no entities in real test"

            blast = _run(channel.get_blast_radius(rows[0].get("id") or env["SRE_TEST_FROM_ID"]))
            assert isinstance(blast, dict)
            assert len(blast) > 0

            path = _run(channel.get_path(env["SRE_TEST_FROM_ID"], env["SRE_TEST_TO_ID"]))
            assert isinstance(path, list)
            assert len(path) > 0
        finally:
            _run(channel.disconnect())
            _run(adapter.aclose())

    def test_real_refresh_entity_write_guard(self) -> None:
        require_real_tests()
        if not allow_write_ops():
            pytest.skip("write operations are disabled (SRE_ALLOW_WRITE_OPS=0)")

        env = require_env(
            "SRE_ONTOLOGY_URL",
            "SRE_ONTOLOGY_QUERY_PATH",
            "SRE_ONTOLOGY_BLAST_PATH",
            "SRE_ONTOLOGY_PATH_PATH",
            "SRE_ONTOLOGY_REFRESH_PATH",
            "SRE_TEST_FROM_ID",
        )
        timeout_sec = get_http_timeout_sec()
        retry_count = get_http_retry_count()

        adapter = OntologyHttpAdapter(
            base_url=env["SRE_ONTOLOGY_URL"],
            query_path=env["SRE_ONTOLOGY_QUERY_PATH"],
            blast_path=env["SRE_ONTOLOGY_BLAST_PATH"],
            path_path=env["SRE_ONTOLOGY_PATH_PATH"],
            refresh_path=env["SRE_ONTOLOGY_REFRESH_PATH"],
            timeout=timeout_sec,
            retries=retry_count,
            headers=build_auth_headers("ONTOLOGY"),
        )
        channel = OntologyChannel(ontology=adapter)

        try:
            _run(channel.connect())
            result = _run(channel.refresh_entity(env["SRE_TEST_FROM_ID"]))
            assert isinstance(result, dict)
            assert len(result) > 0
        finally:
            _run(channel.disconnect())
            _run(adapter.aclose())
