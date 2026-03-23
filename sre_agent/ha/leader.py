"""Backward-compatible HA exports."""

from sre_agent.ha.heartbeat import HAConfig, LeaderLease, RedisLeaderElector, RedisLikeClient
from sre_agent.ha.replication import StateReplicator

__all__ = ["HAConfig", "LeaderLease", "RedisLeaderElector", "RedisLikeClient", "StateReplicator"]
