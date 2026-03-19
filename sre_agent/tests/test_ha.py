from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sre_agent.ha.heartbeat import HAConfig, LeaderLease
from sre_agent.ha.leader import RedisLeaderElector
from sre_agent.ha.replication import StateReplicator


class TestHAUnit:
    def test_unit_exposes_document_field_semantics_when_config_instantiated(self) -> None:
        config = HAConfig(enabled=True, heartbeat_interval=5, heartbeat_timeout=15, redis_url="redis://redis:6379/1")

        assert config.enabled is True
        assert config.heartbeat_interval == 5
        assert config.heartbeat_timeout == 15
        assert config.lease_seconds == 15
        assert config.failover_threshold == 3

    def test_unit_reports_lease_expiry_when_deadline_passed(self) -> None:
        expired = LeaderLease(
            holder_id="node-a",
            acquired_at=datetime.now(UTC) - timedelta(seconds=30),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        active = LeaderLease(
            holder_id="node-a",
            acquired_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )

        assert expired.is_expired is True
        assert active.is_expired is False


class TestHAIntegration:
    @pytest.mark.asyncio
    async def test_integration_blocks_leader_election_when_prod_stub_has_no_client(self) -> None:
        elector = RedisLeaderElector(node_id="node-a")

        with pytest.raises(NotImplementedError):
            await elector.acquire()
        with pytest.raises(NotImplementedError):
            await elector.renew()
        with pytest.raises(NotImplementedError):
            await elector.release()

    @pytest.mark.asyncio
    async def test_integration_blocks_replication_when_prod_stub_not_wired(self) -> None:
        replicator = StateReplicator()

        with pytest.raises(NotImplementedError):
            await replicator.publish("topic", {"step": 1})
        with pytest.raises(NotImplementedError):
            await replicator.replay("topic")


class TestHAE2E:
    @pytest.mark.asyncio
    async def test_e2e_stably_blocks_full_ha_flow_when_backends_unavailable(self) -> None:
        elector = RedisLeaderElector(node_id="node-a")
        replicator = StateReplicator()

        with pytest.raises(NotImplementedError):
            await elector.acquire()
        with pytest.raises(NotImplementedError):
            await replicator.publish("leader-events", {"node_id": "node-a", "state": "active"})

    @pytest.mark.asyncio
    async def test_e2e_keeps_stub_replay_flow_blocked_when_no_replication_backend(self) -> None:
        replicator = StateReplicator()

        with pytest.raises(NotImplementedError):
            await replicator.replay("leader-events", last_event_id="evt-1")
