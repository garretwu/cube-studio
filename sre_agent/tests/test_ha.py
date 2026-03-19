from __future__ import annotations

import asyncio

import pytest

from sre_agent.ha.heartbeat import HAConfig
from sre_agent.ha.leader import RedisLeaderElector
from sre_agent.ha.replication import StateReplicator


def test_ha_config_matches_document_field_names() -> None:
    config = HAConfig(enabled=True, heartbeat_interval=5, heartbeat_timeout=15, redis_url="redis://redis:6379/1")
    assert config.enabled is True
    assert config.heartbeat_interval == 5
    assert config.heartbeat_timeout == 15
    assert config.lease_seconds == 15
    assert config.failover_threshold == 3


def test_ha_interfaces_remain_prod_stubs() -> None:
    async def _run() -> None:
        elector = RedisLeaderElector(node_id="node-a")
        replicator = StateReplicator()

        with pytest.raises(NotImplementedError):
            await elector.acquire()
        with pytest.raises(NotImplementedError):
            await replicator.publish("topic", {"step": 1})

    asyncio.run(_run())
