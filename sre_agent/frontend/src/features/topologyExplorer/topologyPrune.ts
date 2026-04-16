import type { TopologyExplorerResponse, TopologyObject, TopologyRelation, TopologyPath } from "../../api/types";

const AI_SERVICE_ALLOWLIST = [
  "ai-models",
  "aihub",
  "automl",
  "ceph-csi",
  "cilium-secrets",
  "envoy-ai-gateway-system",
  "envoy-gateway-system",
  "infra",
  "istio-system",
  "llm",
  "llm-d",
  "logging",
  "monitoring",
  "nvidia-dcgm",
  "nvidia-device-plugin",
  "storage-system",
] as const;

function normalize(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

function getPodNamespace(pod: TopologyObject) {
  const ns = (pod.attributes as Record<string, unknown> | undefined)?.namespace;
  return typeof ns === "string" ? ns.trim() : undefined;
}

function collectServiceAdjacency(edges: TopologyRelation[]) {
  const byNode = new Map<string, Set<string>>();
  edges.forEach((edge) => {
    const listA = byNode.get(edge.source) ?? new Set<string>();
    listA.add(edge.target);
    byNode.set(edge.source, listA);

    const listB = byNode.get(edge.target) ?? new Set<string>();
    listB.add(edge.source);
    byNode.set(edge.target, listB);
  });
  return byNode;
}

function prunePaths(paths: TopologyPath[], keepNodeIds: Set<string>, keepEdgeIds: Set<string>) {
  return paths
    .map((path) => {
      const entryOk = keepNodeIds.has(path.entryNodeId);
      const rootOk = keepNodeIds.has(path.rootCauseNodeId);
      if (!entryOk || !rootOk) {
        return null;
      }

      const affectedNodeIds = path.affectedNodeIds.filter((id) => keepNodeIds.has(id));
      const edgeIds = path.edgeIds.filter((id) => keepEdgeIds.has(id));
      return { ...path, affectedNodeIds, edgeIds };
    })
    .filter((path): path is TopologyPath => Boolean(path));
}

export function pruneTopologyExplorerResponse(raw: TopologyExplorerResponse): TopologyExplorerResponse {
  const allowlist = new Set(AI_SERVICE_ALLOWLIST.map((name) => normalize(name)));

  const serviceNodes = raw.nodes.filter((node) => node.type === "service");
  const hasAllowlistedService = serviceNodes.some((service) => {
    const name = normalize(service.name);
    if (allowlist.has(name)) {
      return true;
    }
    const attrNs = normalize((service.attributes as any)?.namespace);
    if (attrNs && allowlist.has(attrNs)) {
      return true;
    }
    const id = normalize(service.id);
    return allowlist.has(id);
  });
  if (!hasAllowlistedService) {
    return raw;
  }

  const keptServiceIds = new Set(
    serviceNodes
      .filter((service) => {
        const name = normalize(service.name);
        if (allowlist.has(name)) {
          return true;
        }
        const attrNs = normalize((service.attributes as any)?.namespace);
        if (attrNs && allowlist.has(attrNs)) {
          return true;
        }
        const id = normalize(service.id);
        return allowlist.has(id);
      })
      .map((service) => service.id),
  );

  const removedServiceIds = new Set(serviceNodes.filter((node) => !keptServiceIds.has(node.id)).map((node) => node.id));
  if (removedServiceIds.size === 0) {
    return raw;
  }

  const adjacency = collectServiceAdjacency(raw.edges);
  const keptPodIds = new Set<string>();
  const removedPodIds = new Set<string>();

  raw.nodes.forEach((node) => {
    if (node.type !== "pod") {
      return;
    }

    const ns = getPodNamespace(node);
    if (ns && allowlist.has(normalize(ns))) {
      keptPodIds.add(node.id);
      return;
    }

    const neighbors = adjacency.get(node.id);
    if (!neighbors || neighbors.size === 0) {
      removedPodIds.add(node.id);
      return;
    }

    const touchesRemovedService = Array.from(neighbors).some((neighborId) => removedServiceIds.has(neighborId));
    const touchesKeptService = Array.from(neighbors).some((neighborId) => keptServiceIds.has(neighborId));

    // If a pod is attached to any removed service, drop it.
    // Otherwise keep pods that are still attached to a kept service; drop everything else to reduce noise.
    if (touchesRemovedService) {
      removedPodIds.add(node.id);
      return;
    }

    if (touchesKeptService) {
      keptPodIds.add(node.id);
      return;
    }

    removedPodIds.add(node.id);
  });

  const keepNodeIds = new Set<string>();
  raw.nodes.forEach((node) => {
    if (node.type === "service") {
      if (keptServiceIds.has(node.id)) {
        keepNodeIds.add(node.id);
      }
      return;
    }
    if (node.type === "pod") {
      if (keptPodIds.has(node.id)) {
        keepNodeIds.add(node.id);
      }
      return;
    }
    keepNodeIds.add(node.id);
  });

  const nodes = raw.nodes.filter((node) => keepNodeIds.has(node.id));
  const edges = raw.edges.filter((edge) => keepNodeIds.has(edge.source) && keepNodeIds.has(edge.target));
  const keepEdgeIds = new Set(edges.map((edge) => edge.id));
  const paths = prunePaths(raw.paths, keepNodeIds, keepEdgeIds);

  return { ...raw, nodes, edges, paths };
}

