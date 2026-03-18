from __future__ import annotations

import asyncio

import pytest

from sre_agent.ha.leader import RedisLeaderElector
from sre_agent.memory.store_pg import MemoryStorePG
from sre_agent.slo.degradation import DegradationMode, SLODegradationPolicy
from sre_agent.slo.metrics import SLOMetricsSnapshot


def test_memory_store_pg_is_stub() -> None:
    async def _run() -> None:
        store = MemoryStorePG(aidc_id="aidc", pg_dsn="postgresql://demo", qdrant_url="http://qdrant")
        with pytest.raises(NotImplementedError):
            await store.connect()

    asyncio.run(_run())


def test_redis_leader_elector_requires_prod_client() -> None:
    async def _run() -> None:
        elector = RedisLeaderElector(node_id="node-a")
        with pytest.raises(NotImplementedError):
            await elector.acquire()

    asyncio.run(_run())


def test_slo_degradation_policy_modes() -> None:
    policy = SLODegradationPolicy()
    assert policy.evaluate(SLOMetricsSnapshot()) == DegradationMode.FULL
    assert policy.evaluate({"diagnosis_success_rate": 0.8, "false_fix_rate": 0.01, "llm_success_rate": 0.99}) == DegradationMode.DEGRADED
    assert policy.evaluate({"diagnosis_success_rate": 0.6, "false_fix_rate": 0.11, "llm_success_rate": 0.99}) == DegradationMode.MINIMAL
    assert policy.evaluate({"diagnosis_success_rate": 0.95, "false_fix_rate": 0.2, "llm_success_rate": 0.99}) == DegradationMode.EMERGENCY
    assert policy.get_effective_approval_policy() == "disabled"
