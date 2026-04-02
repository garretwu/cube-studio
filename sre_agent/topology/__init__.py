"""Topology helpers for discovery and runtime diagnosis context."""

from .discovery import (
    discover_live_snapshot,
    discover_static_snapshot,
    load_live_inventory,
    scan_live_sources,
)
from .runtime_context import (
    augment_runtime_ontology,
    build_topology_context,
    infer_node_name_from_inventory_ip,
    resolve_node_ip_from_pod,
    resolve_pod_name_from_service,
)

__all__ = [
    "discover_live_snapshot",
    "discover_static_snapshot",
    "load_live_inventory",
    "scan_live_sources",
    "augment_runtime_ontology",
    "build_topology_context",
    "infer_node_name_from_inventory_ip",
    "resolve_node_ip_from_pod",
    "resolve_pod_name_from_service",
]
