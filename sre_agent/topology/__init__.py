"""Topology runtime utilities."""

from sre_agent.topology.discovery import (
    discover_live_snapshot,
    discover_static_snapshot,
    load_live_inventory,
    scan_live_sources,
)

__all__ = [
    "discover_live_snapshot",
    "discover_static_snapshot",
    "load_live_inventory",
    "scan_live_sources",
]

