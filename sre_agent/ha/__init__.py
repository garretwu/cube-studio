"""High-availability interfaces."""

from sre_agent.ha.heartbeat import HAConfig, LeaderLease, RedisLeaderElector
from sre_agent.ha.replication import StateReplicator

__all__ = ["HAConfig", "LeaderLease", "RedisLeaderElector", "StateReplicator"]
