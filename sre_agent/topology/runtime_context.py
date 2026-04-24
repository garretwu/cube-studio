"""Runtime topology discovery and context assembly helpers."""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from lib.channels.kubernetes import K8sChannel
from lib.channels.ontology import OntologyChannel
from lib.channels.ssh import SSHChannel
from lib.channels.switch import SwitchChannel
from sre_agent.models.alert import Alert
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.tools import ToolExecutionContext


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


async def resolve_pod_name_from_service(
    *,
    k8s_channel: K8sChannel | None,
    namespace: str,
    service: str,
) -> str:
    if not isinstance(k8s_channel, K8sChannel) or not namespace or not service:
        return ""
    try:
        pods = await k8s_channel.resolve_pod_names_for_service(namespace, service)
    except Exception:
        return ""
    names = sorted(str(item).strip() for item in pods if str(item).strip())
    return names[0] if names else ""


async def resolve_node_ip_from_pod(
    *,
    k8s_channel: K8sChannel | None,
    namespace: str,
    pod_name: str,
) -> str:
    if not isinstance(k8s_channel, K8sChannel) or not namespace or not pod_name:
        return ""
    try:
        value = await k8s_channel.resolve_node_ip_for_pod(namespace, pod_name)
    except Exception:
        return ""
    return str(value or "").strip()


def infer_node_name_from_inventory_ip(inventory: dict[str, Any], node_ip: str) -> str:
    ip = str(node_ip or "").strip()
    if not ip:
        return ""
    for node_name, cfg in inventory.items():
        ssh_cfg = getattr(cfg, "ssh", None)
        host = str(getattr(ssh_cfg, "host", "") or "").strip()
        if host == ip:
            return str(node_name)
    return ""


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _safe_entity_id(*parts: str) -> str:
    clean = [re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(part).strip()) for part in parts if str(part).strip()]
    return ":".join(clean)


def _parse_gpu_metric_rows(raw_output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            index = int(parts[0])
            util_pct = float(parts[2])
            mem_used = float(parts[3])
            mem_total = float(parts[4])
            temp_c = float(parts[5])
        except ValueError:
            continue
        rows.append(
            {
                "index": index,
                "name": parts[1],
                "utilization_gpu_pct": util_pct,
                "memory_used_mb": mem_used,
                "memory_total_mb": mem_total,
                "temperature_c": temp_c,
            }
        )
    return rows


def _parse_rdma_link_rows(raw_output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"link\s+(?P<link>\S+)\s+state\s+(?P<state>\S+)\s+physical_state\s+(?P<physical_state>\S+)\s+netdev\s+(?P<netdev>\S+)",
        re.IGNORECASE,
    )
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        match = pattern.search(line)
        if not match:
            continue
        rows.append(
            {
                "link": match.group("link"),
                "state": match.group("state"),
                "physical_state": match.group("physical_state"),
                "netdev": match.group("netdev"),
            }
        )
    return rows


async def _scan_runtime_resource_topology(
    *,
    context: ToolExecutionContext,
    node: str,
) -> dict[str, Any]:
    scan: dict[str, Any] = {
        "gpu_rows": [],
        "rdma_links": [],
        "switches": [],
    }

    ssh_channel = context.channels.get("ssh")
    if isinstance(ssh_channel, SSHChannel) and node:
        try:
            gpu_cmd = (
                "nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu "
                "--format=csv,noheader,nounits"
            )
            gpu_output = await ssh_channel.run_command(node, gpu_cmd)
            scan["gpu_rows"] = _parse_gpu_metric_rows(str(gpu_output or ""))
        except Exception as exc:  # noqa: BLE001
            scan["gpu_scan_error"] = str(exc)

        try:
            rdma_output = await ssh_channel.run_command(node, "rdma link show; cat /proc/net/softnet_stat | head -n 4")
            scan["rdma_links"] = _parse_rdma_link_rows(str(rdma_output or ""))
        except Exception as exc:  # noqa: BLE001
            scan["rdma_scan_error"] = str(exc)

    switch_channel = context.channels.get("switch")
    if isinstance(switch_channel, SwitchChannel):
        switches: list[dict[str, Any]] = []
        for switch_name in sorted(switch_channel.devices.keys()):
            entry: dict[str, Any] = {"name": switch_name}
            try:
                entry["interfaces"] = _to_jsonable(switch_channel.get_all_interfaces(switch_name))
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
            switches.append(entry)
        scan["switches"] = switches

    return scan


async def augment_runtime_ontology(
    *,
    context: ToolExecutionContext,
    alert: Alert,
    namespace: str,
    service: str,
    pod_name: str,
    node: str,
    node_ip: str,
    latency_value_ms: float | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"seeded": False}
    ontology_channel = context.channels.get("ontology")
    graph = getattr(ontology_channel, "ontology", None)
    if not isinstance(ontology_channel, OntologyChannel) or not isinstance(graph, OntologyGraph):
        summary["reason"] = "ontology channel unavailable"
        return summary

    if not namespace or not service or not pod_name or not node:
        summary["reason"] = "insufficient service/pod/node context"
        return summary

    resource_scan = await _scan_runtime_resource_topology(context=context, node=node)

    k8s_channel = context.channels.get("k8s")
    pod_phase = None
    pod_labels: dict[str, Any] = {}
    if isinstance(k8s_channel, K8sChannel):
        try:
            pods = await k8s_channel.list_pods(namespace)
        except Exception:
            pods = []
        for pod in pods:
            if not isinstance(pod, dict):
                continue
            candidate_name = str(pod.get("name") or pod.get("metadata", {}).get("name") or "").strip()
            if candidate_name != pod_name:
                continue
            metadata = pod.get("metadata", {})
            if isinstance(metadata, dict):
                labels = metadata.get("labels", {})
                if isinstance(labels, dict):
                    pod_labels = dict(labels)
            status = pod.get("status", {})
            if isinstance(status, dict):
                pod_phase = status.get("phase")
            break

    model_name = str(alert.labels.get("model_name") or "").strip()
    service_entity_id = f"service:{namespace}:{service}"
    pod_entity_id = f"pod:{namespace}:{pod_name}"
    metric_entity_id = f"metric:{alert.alert_name}:{namespace}:{service}"

    nodes = [
        OntologyNode(
            id=service_entity_id,
            entity_type=EntityType.INFERENCE_SERVICE,
            name=service,
            properties={
                "namespace": namespace,
                "service": service,
                "model_name": model_name or None,
                "source": "live_demo_runtime",
            },
            status="degraded" if alert.status.value == "firing" else "unknown",
            updated_at=_now_utc(),
        ),
        OntologyNode(
            id=pod_entity_id,
            entity_type=EntityType.K8S_POD,
            name=pod_name,
            properties={
                "namespace": namespace,
                "service": service,
                "labels": pod_labels,
                "source": "live_demo_runtime",
            },
            status=str(pod_phase or "Unknown"),
            updated_at=_now_utc(),
        ),
        OntologyNode(
            id=node,
            entity_type=EntityType.NODE,
            name=node,
            properties={"ip": node_ip, "source": "live_demo_runtime"},
            status="online",
            updated_at=_now_utc(),
        ),
        OntologyNode(
            id=metric_entity_id,
            entity_type=EntityType.METRIC_ENDPOINT,
            name=alert.alert_name,
            properties={
                "metric": alert.alert_name,
                "latest_value_ms": latency_value_ms,
                "severity": alert.severity.value,
                "source": "live_demo_runtime",
            },
            status="fresh",
            updated_at=_now_utc(),
        ),
    ]
    edges = [
        OntologyEdge(
            source_id=service_entity_id,
            target_id=pod_entity_id,
            relation=RelationType.SERVES,
            properties={"namespace": namespace, "source": "live_demo_runtime"},
        ),
        OntologyEdge(
            source_id=pod_entity_id,
            target_id=node,
            relation=RelationType.HOSTED_ON,
            properties={"namespace": namespace, "source": "live_demo_runtime"},
        ),
        OntologyEdge(
            source_id=metric_entity_id,
            target_id=service_entity_id,
            relation=RelationType.MONITORS,
            properties={"alert_name": alert.alert_name, "source": "live_demo_runtime"},
        ),
    ]

    gpu_entity_ids: list[str] = []
    for gpu_row in resource_scan.get("gpu_rows", []):
        if not isinstance(gpu_row, dict):
            continue
        gpu_index = gpu_row.get("index")
        gpu_id = _safe_entity_id("gpu", node, str(gpu_index))
        gpu_entity_ids.append(gpu_id)
        nodes.append(
            OntologyNode(
                id=gpu_id,
                entity_type=EntityType.GPU,
                name=f"GPU-{gpu_index}",
                properties={**gpu_row, "node": node, "source": "live_demo_runtime"},
                status="hot" if float(gpu_row.get("utilization_gpu_pct") or 0) >= 80 else "normal",
                updated_at=_now_utc(),
            )
        )
        edges.append(
            OntologyEdge(
                source_id=node,
                target_id=gpu_id,
                relation=RelationType.PART_OF,
                properties={"source": "live_demo_runtime"},
            )
        )

    nic_entity_ids: list[str] = []
    switch_port_entity_ids: list[str] = []
    for rdma_link in resource_scan.get("rdma_links", []):
        if not isinstance(rdma_link, dict):
            continue
        link = str(rdma_link.get("link") or "").strip()
        if not link:
            continue
        nic_id = _safe_entity_id("nic", node, link)
        nic_entity_ids.append(nic_id)
        nodes.append(
            OntologyNode(
                id=nic_id,
                entity_type=EntityType.NIC,
                name=link,
                properties={**rdma_link, "node": node, "kind": "rdma", "source": "live_demo_runtime"},
                status="active" if str(rdma_link.get("state") or "").upper() == "ACTIVE" else "down",
                updated_at=_now_utc(),
            )
        )
        edges.append(
            OntologyEdge(
                source_id=node,
                target_id=nic_id,
                relation=RelationType.PART_OF,
                properties={"source": "live_demo_runtime"},
            )
        )
        switch_port_id = _safe_entity_id("switch_port", node, link)
        switch_port_entity_ids.append(switch_port_id)
        nodes.append(
            OntologyNode(
                id=switch_port_id,
                entity_type=EntityType.SWITCH_PORT,
                name=link,
                properties={"node": node, "nic_link": link, "source": "live_demo_runtime"},
                status="connected",
                updated_at=_now_utc(),
            )
        )
        edges.append(
            OntologyEdge(
                source_id=switch_port_id,
                target_id=nic_id,
                relation=RelationType.CONNECTED_TO,
                properties={"source": "live_demo_runtime"},
            )
        )

    switch_entity_ids: list[str] = []
    for switch_entry in resource_scan.get("switches", []):
        if not isinstance(switch_entry, dict):
            continue
        switch_name = str(switch_entry.get("name") or "").strip()
        if not switch_name:
            continue
        switch_id = _safe_entity_id("switch", switch_name)
        switch_entity_ids.append(switch_id)
        nodes.append(
            OntologyNode(
                id=switch_id,
                entity_type=EntityType.SWITCH,
                name=switch_name,
                properties={"source": "live_demo_runtime"},
                status="online" if not switch_entry.get("error") else "unknown",
                updated_at=_now_utc(),
            )
        )
        for interface in switch_entry.get("interfaces", []) or []:
            if not isinstance(interface, dict):
                continue
            port_name = str(interface.get("abbreviated_name") or interface.get("name") or "").strip()
            if not port_name:
                continue
            switch_port_id = _safe_entity_id("switch_port", switch_name, port_name)
            switch_port_entity_ids.append(switch_port_id)
            nodes.append(
                OntologyNode(
                    id=switch_port_id,
                    entity_type=EntityType.SWITCH_PORT,
                    name=port_name,
                    properties={**interface, "switch": switch_name, "source": "live_demo_runtime"},
                    status=str(interface.get("oper_status") or "unknown"),
                    updated_at=_now_utc(),
                )
            )
            edges.append(
                OntologyEdge(
                    source_id=switch_port_id,
                    target_id=switch_id,
                    relation=RelationType.PART_OF,
                    properties={"source": "live_demo_runtime"},
                )
            )
            description = str(interface.get("description") or "").lower()
            if node.lower() in description:
                edges.append(
                    OntologyEdge(
                        source_id=switch_port_id,
                        target_id=node,
                        relation=RelationType.CONNECTED_TO,
                        properties={"source": "live_demo_runtime", "match": "description"},
                    )
                )

    for ontology_node in nodes:
        await graph.add_node(ontology_node)
    for ontology_edge in edges:
        await graph.add_edge(ontology_edge)

    summary.update(
        {
            "seeded": True,
            "service_entity_id": service_entity_id,
            "pod_entity_id": pod_entity_id,
            "node_entity_id": node,
            "metric_entity_id": metric_entity_id,
            "gpu_entity_ids": gpu_entity_ids,
            "nic_entity_ids": nic_entity_ids,
            "switch_entity_ids": sorted(set(switch_entity_ids)),
            "switch_port_entity_ids": sorted(set(switch_port_entity_ids)),
            "resource_scan": resource_scan,
        }
    )
    return summary


def _summarize_ontology_entity(entity: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entity, dict):
        return None
    properties = entity.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    return {
        "id": entity.get("id"),
        "entity_type": entity.get("entity_type") or entity.get("type"),
        "name": entity.get("name"),
        "status": entity.get("status"),
        "properties": properties,
    }


async def _find_ontology_entity(
    channel: OntologyChannel | None,
    *,
    entity_type: str,
    filters_list: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if channel is None:
        return None
    for filters in filters_list:
        clean_filters = {
            str(key): value
            for key, value in filters.items()
            if value not in (None, "", [], {})
        }
        if not clean_filters:
            continue
        try:
            rows = await channel.query(entity_type, filters=clean_filters)
        except Exception:
            continue
        if rows:
            return rows[0]
    return None


async def build_topology_context(
    *,
    context: ToolExecutionContext,
    inventory: dict[str, Any],
    switch_devices: dict[str, dict[str, Any]],
    namespace: str,
    service: str,
    pod_name: str,
    node: str,
    node_ip: str,
    runtime_ontology: dict[str, Any] | None = None,
) -> dict[str, Any]:
    topology: dict[str, Any] = {
        "scope": "alert-derived-topology",
        "namespace": namespace,
        "service": service,
        "pod_name": pod_name,
        "node": node,
        "node_ip": node_ip,
    }

    node_cfg = inventory.get(node)
    if node_cfg is not None:
        ssh_cfg = getattr(node_cfg, "ssh", None)
        topology["inventory_node"] = {
            "name": node,
            "ssh_host": str(getattr(ssh_cfg, "host", "") or "").strip() or None,
            "interface": getattr(node_cfg, "interface", None),
            "roles": list(getattr(node_cfg, "roles", []) or []),
        }

    k8s_channel = context.channels.get("k8s")
    if isinstance(k8s_channel, K8sChannel) and namespace and service:
        try:
            service_pods = await k8s_channel.resolve_pod_names_for_service(namespace, service)
        except Exception as exc:  # noqa: BLE001
            topology["service_pod_resolution_error"] = str(exc)
        else:
            topology["service_pods"] = sorted(str(item) for item in service_pods if str(item).strip())

    ontology_channel = context.channels.get("ontology")
    ontology_summary: dict[str, Any] = {}
    if isinstance(ontology_channel, OntologyChannel):
        service_entity = await _find_ontology_entity(
            ontology_channel,
            entity_type="inference_service",
            filters_list=[
                {"name": service, "namespace": namespace},
                {"name": service},
                {"service": service, "namespace": namespace},
                {"service": service},
                {"id": service},
            ],
        )
        pod_entity = await _find_ontology_entity(
            ontology_channel,
            entity_type="k8s_pod",
            filters_list=[
                {"name": pod_name, "namespace": namespace},
                {"name": pod_name},
                {"id": f"pod:{namespace}:{pod_name}"},
            ],
        )
        node_entity = await _find_ontology_entity(
            ontology_channel,
            entity_type="node",
            filters_list=[
                {"id": node},
                {"name": node},
                {"ip": node_ip},
                {"host": node},
            ],
        )

        ontology_summary["service_entity"] = _summarize_ontology_entity(service_entity)
        ontology_summary["pod_entity"] = _summarize_ontology_entity(pod_entity)
        ontology_summary["node_entity"] = _summarize_ontology_entity(node_entity)

        service_entity_id = str((service_entity or {}).get("id") or "").strip()
        pod_entity_id = str((pod_entity or {}).get("id") or "").strip()
        node_entity_id = str((node_entity or {}).get("id") or "").strip()

        try:
            if service_entity_id and node_entity_id:
                ontology_summary["service_to_node_path"] = await ontology_channel.get_path(
                    service_entity_id,
                    node_entity_id,
                )
        except Exception as exc:  # noqa: BLE001
            ontology_summary["service_to_node_path_error"] = str(exc)

        try:
            if pod_entity_id and node_entity_id:
                ontology_summary["pod_to_node_path"] = await ontology_channel.get_path(
                    pod_entity_id,
                    node_entity_id,
                )
        except Exception as exc:  # noqa: BLE001
            ontology_summary["pod_to_node_path_error"] = str(exc)

        blast_entity_id = node_entity_id or service_entity_id or pod_entity_id
        try:
            if blast_entity_id:
                blast = await ontology_channel.get_blast_radius(blast_entity_id)
                affected_entities = blast.get("affected_entities", [])
                ontology_summary["blast_radius"] = {
                    "root_entity_id": blast.get("root_entity_id"),
                    "affected_count": blast.get("affected_count"),
                    "affected_entities": [
                        _summarize_ontology_entity(entity)
                        for entity in affected_entities
                        if isinstance(entity, dict)
                    ],
                }
        except Exception as exc:  # noqa: BLE001
            ontology_summary["blast_radius_error"] = str(exc)

        subject_entity_id = pod_entity_id or node_entity_id or service_entity_id
        if subject_entity_id:
            try:
                neighbors = await ontology_channel.get_neighbors(subject_entity_id)
                direct_relations: list[dict[str, Any]] = []
                for neighbor in neighbors:
                    entity = neighbor.get("entity", {})
                    edge = neighbor.get("relation", {})
                    if not isinstance(entity, dict) or not isinstance(edge, dict):
                        continue
                    direct_relations.append({
                        "source": subject_entity_id,
                        "target": str(entity.get("id", "")),
                        "target_type": str(entity.get("entity_type", "")),
                        "target_name": str(entity.get("name", "")),
                        "relation": str(edge.get("relation", "")),
                        "direction": neighbor.get("direction", "out"),
                    })
                if direct_relations:
                    topology["direct_relations"] = direct_relations
            except Exception:  # noqa: BLE001
                pass

    if ontology_summary:
        topology["ontology"] = ontology_summary

    if switch_devices:
        topology["switches"] = sorted(switch_devices.keys())

    runtime_meta = runtime_ontology or {}
    if runtime_meta:
        topology["runtime_ontology"] = {
            key: value
            for key, value in runtime_meta.items()
            if key != "resource_scan"
        }
        scan = runtime_meta.get("resource_scan")
        if isinstance(scan, dict):
            gpu_rows = scan.get("gpu_rows")
            if isinstance(gpu_rows, list):
                topology["gpus"] = gpu_rows
            rdma_links = scan.get("rdma_links")
            if isinstance(rdma_links, list):
                topology["rdma_links"] = rdma_links
            switches_runtime = scan.get("switches")
            if isinstance(switches_runtime, list) and switches_runtime:
                topology["switches_runtime"] = switches_runtime

    summary_parts = [
        f"service {service} in namespace {namespace}",
        f"resolved pod {pod_name}",
        f"hosted on node {node} ({node_ip})",
    ]
    if topology.get("service_pods"):
        summary_parts.append(f"service currently resolves to pods {topology['service_pods']}")
    if topology.get("gpus"):
        summary_parts.append(f"scanned {len(topology['gpus'])} GPUs on the target node")
    if topology.get("rdma_links"):
        summary_parts.append(
            f"scanned {len(topology['rdma_links'])} RDMA links and inferred switch-port adjacencies"
        )
    if topology.get("switches_runtime"):
        summary_parts.append(f"scanned {len(topology['switches_runtime'])} switch devices through switch channel")
    blast = ((topology.get("ontology") or {}).get("blast_radius") or {})
    if blast.get("affected_count"):
        summary_parts.append(f"ontology blast radius shows {blast['affected_count']} related entities")
    topology["summary"] = "; ".join(summary_parts)
    return topology
