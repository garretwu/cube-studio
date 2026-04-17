import type {
  TopologyExplorerResponse,
  TopologyObject,
  TopologyObjectStatus,
  TopologyObjectType,
  TopologyRelation,
  TopologySite,
} from "../api/types";
import rawTopologyCanvas from "./topologyExplorerOnlineData.json";

type RawTopologyCanvasNode = {
  id: string;
  name?: string | null;
  type: string;
  status?: string | null;
  layer: TopologyObject["layer"];
  domain?: string | null;
  region?: string | null;
  zone?: string | null;
  cluster?: string | null;
  rack?: string | null;
  slot?: string | null;
  summary?: string | null;
  tags?: string[] | null;
  metrics?: Record<string, string | number | null> | null;
  attributes?: Record<string, unknown> | null;
  position?: {
    x: number;
    y: number;
  } | null;
  size?: {
    width: number;
    height: number;
  } | null;
};

type RawTopologyCanvasEdge = {
  id: string;
  source: string;
  target: string;
  relationType: TopologyRelation["relationType"] | string;
  status?: string | null;
  impactLevel?: TopologyRelation["impactLevel"] | string | null;
  label?: string | null;
  isCritical?: boolean | null;
  isAggregated?: boolean | null;
};

type RawTopologyCanvasResponse = {
  meta?: {
    exportedAt?: string | null;
    lastUpdated?: string | null;
  } | null;
  nodes?: RawTopologyCanvasNode[] | null;
  edges?: RawTopologyCanvasEdge[] | null;
};

function pickMostCommon(values: Array<string | null | undefined>, fallback: string) {
  const counts = new Map<string, number>();

  values.forEach((value) => {
    const normalized = String(value ?? "").trim();
    if (!normalized) {
      return;
    }

    counts.set(normalized, (counts.get(normalized) ?? 0) + 1);
  });

  const winner = Array.from(counts.entries()).sort((left, right) => {
    if (right[1] !== left[1]) {
      return right[1] - left[1];
    }
    return left[0].localeCompare(right[0]);
  })[0]?.[0];

  return winner ?? fallback;
}

function mapNodeType(node: RawTopologyCanvasNode): TopologyObjectType {
  const normalized = node.type.trim().toLowerCase();
  const normalizedSummary = String(node.summary ?? "").trim().toLowerCase();

  if (normalized.includes("bmc") || normalizedSummary.includes("bmc_endpoint")) {
    return "bmc";
  }
  if (normalized.includes("port") || normalizedSummary.includes("switch_port")) {
    return "port";
  }
  if (normalized === "pod") {
    return "pod";
  }
  if (normalized === "service") {
    return "service";
  }
  if (normalized === "switch") {
    return "switch";
  }
  if (normalized === "cluster") {
    return "cluster";
  }
  if (normalized === "gpu") {
    return "gpu";
  }
  if (normalized === "rack") {
    return "rack";
  }
  return "node";
}

function mapStatus(status: string | null | undefined): TopologyObjectStatus {
  const normalized = String(status ?? "").trim().toLowerCase();

  if (normalized === "abnormal") {
    return "abnormal";
  }
  if (normalized === "impacted") {
    return "impacted";
  }
  if (normalized === "maintenance") {
    return "maintenance";
  }
  return "healthy";
}

function mapRelationType(relationType: string): TopologyRelation["relationType"] {
  const normalized = relationType.trim().toLowerCase();

  if (normalized === "contains") {
    return "contains";
  }
  if (normalized === "runs_on") {
    return "runs_on";
  }
  if (normalized === "connects_to") {
    return "connects_to";
  }
  if (normalized === "uplink_to") {
    return "uplink_to";
  }
  if (normalized === "aggregated") {
    return "aggregated";
  }
  return "depends_on";
}

function mapImpactLevel(impactLevel: string | null | undefined): TopologyRelation["impactLevel"] {
  const normalized = String(impactLevel ?? "").trim().toLowerCase();

  if (normalized === "high") {
    return "high";
  }
  if (normalized === "medium") {
    return "medium";
  }
  return "low";
}

const topologyCanvas = rawTopologyCanvas as RawTopologyCanvasResponse;
const rawNodes = topologyCanvas.nodes ?? [];
const rawEdges = topologyCanvas.edges ?? [];
const serviceNodeIds = new Set(
  rawNodes
    .filter((node) => String(node.type ?? "").trim().toLowerCase() === "service")
    .map((node) => node.id),
);
const filteredRawNodes = rawNodes.filter((node) => !serviceNodeIds.has(node.id));
const filteredRawEdges = rawEdges.filter(
  (edge) => !serviceNodeIds.has(edge.source) && !serviceNodeIds.has(edge.target),
);

type RawNodeIdRewrite = {
  targetId: string;
  applyWorkerLayout: boolean;
};

const workerRewriteMap: Record<string, RawNodeIdRewrite> = {
  "worker-01": { targetId: "wj-lab-ctl-01", applyWorkerLayout: true },
  "worker-02": { targetId: "wj-lab-ctl-02", applyWorkerLayout: true },
  "worker-03": { targetId: "wj-lab-ctl-03", applyWorkerLayout: true },
  "worker-04": { targetId: "wj-lab-cpt-02", applyWorkerLayout: true },
  "worker-05": { targetId: "wj-lab-cpt-03", applyWorkerLayout: true },
  "worker-06": { targetId: "wj-lab-cpt-04", applyWorkerLayout: true },
};

function rewriteId(id: string) {
  return workerRewriteMap[id]?.targetId ?? id;
}

function mergeNode(workerNode: RawTopologyCanvasNode, targetNode: RawTopologyCanvasNode) {
  return {
    ...targetNode,
    status: workerNode.status ?? targetNode.status,
    layer: workerNode.layer ?? targetNode.layer,
    cluster: workerNode.cluster ?? targetNode.cluster,
    rack: workerNode.rack ?? targetNode.rack,
    slot: workerNode.slot ?? targetNode.slot,
    summary: workerNode.summary ?? targetNode.summary,
    tags: Array.from(new Set([...(targetNode.tags ?? []), ...(workerNode.tags ?? [])])),
    metrics: workerNode.metrics ?? targetNode.metrics,
    attributes: {
      ...(targetNode.attributes ?? {}),
      ...(workerNode.attributes ?? {}),
    },
    position: workerNode.position ?? targetNode.position,
    size: workerNode.size ?? targetNode.size,
  } satisfies RawTopologyCanvasNode;
}

const nodeById = new Map(filteredRawNodes.map((node) => [node.id, node]));
const mergedNodeIds = new Set<string>();
Object.entries(workerRewriteMap).forEach(([workerId, rule]) => {
  const workerNode = nodeById.get(workerId);
  const targetNode = nodeById.get(rule.targetId);
  if (!workerNode || !targetNode) {
    return;
  }
  nodeById.set(rule.targetId, mergeNode(workerNode, targetNode));
  mergedNodeIds.add(workerId);
});

const canonicalRawNodes = Array.from(nodeById.values()).filter((node) => !mergedNodeIds.has(node.id));
const canonicalRawEdges = filteredRawEdges
  .map((edge) => ({
    ...edge,
    source: rewriteId(edge.source),
    target: rewriteId(edge.target),
  }))
  .filter((edge) => edge.source !== edge.target);

function ensureClusterSwitchEdge(
  nodes: RawTopologyCanvasNode[],
  edges: RawTopologyCanvasEdge[],
) {
  const clusterId = nodes.find((node) => String(node.type ?? "").trim().toLowerCase() === "cluster")?.id;
  const switchId = nodes.find((node) => String(node.type ?? "").trim().toLowerCase() === "switch")?.id;
  if (!clusterId || !switchId) {
    return edges;
  }
  const exists = edges.some(
    (edge) =>
      (edge.source === clusterId && edge.target === switchId) ||
      (edge.source === switchId && edge.target === clusterId),
  );
  if (exists) {
    return edges;
  }
  return [
    ...edges,
    {
      id: `edge-synthetic-${clusterId}-${switchId}`,
      source: clusterId,
      target: switchId,
      relationType: "connects_to",
      status: "healthy",
      impactLevel: "low",
      label: "connects_to",
      isCritical: false,
      isAggregated: false,
    },
  ];
}

function ensurePortSwitchEdges(nodes: RawTopologyCanvasNode[], edges: RawTopologyCanvasEdge[]) {
  const switchIds = new Set(
    nodes
      .filter((node) => String(node.type ?? "").trim().toLowerCase() === "switch")
      .map((node) => node.id),
  );
  const primarySwitchId = nodes.find((node) => String(node.type ?? "").trim().toLowerCase() === "switch")?.id;

  if (!primarySwitchId) {
    return edges;
  }

  const portNodes = nodes.filter((node) => String(node.type ?? "").trim().toLowerCase().includes("port"));

  const syntheticEdges: RawTopologyCanvasEdge[] = [];
  portNodes.forEach((port) => {
    const portId = port.id;
    const inferredSwitchFromId = portId.includes(":") ? portId.split(":")[0] : undefined;
    const switchId =
      (inferredSwitchFromId && switchIds.has(inferredSwitchFromId) ? inferredSwitchFromId : undefined) ??
      (typeof (port.attributes as any)?.switch === "string" && switchIds.has((port.attributes as any).switch)
        ? ((port.attributes as any).switch as string)
        : undefined) ??
      primarySwitchId;

    const exists = edges.some(
      (edge) =>
        (edge.source === portId && edge.target === switchId) ||
        (edge.source === switchId && edge.target === portId),
    );
    if (exists) {
      return;
    }

    syntheticEdges.push({
      id: `edge-synthetic-${portId}-${switchId}`,
      source: portId,
      target: switchId,
      relationType: "contains",
      status: "healthy",
      impactLevel: "low",
      label: "part_of",
      isCritical: false,
      isAggregated: false,
    });
  });

  return syntheticEdges.length ? [...edges, ...syntheticEdges] : edges;
}

function buildNamespaceServices(
  nodes: RawTopologyCanvasNode[],
  edges: RawTopologyCanvasEdge[],
) {
  const podNodes = nodes.filter((node) => String(node.type ?? "").trim().toLowerCase() === "pod");
  const namespaces = Array.from(
    new Set(
      podNodes
        .map((node) => (node.attributes as any)?.namespace)
        .filter((ns): ns is string => typeof ns === "string" && ns.trim().length > 0),
    ),
  ).sort((a, b) => a.localeCompare(b));

  if (namespaces.length === 0) {
    return { nodes, edges };
  }

  const serviceNodes: RawTopologyCanvasNode[] = namespaces.map((ns) => ({
    id: `svc-ns:${ns}`,
    name: ns,
    type: "service",
    status: "healthy",
    layer: "service",
    domain: "aidc",
    region: "AIDC-CN",
    zone: "zone-a",
    cluster: "k8s:aidc-lab",
    rack: null,
    slot: null,
    summary: `${ns} (namespace_service)`,
    tags: [ns, "synthetic", "namespace-service"],
    metrics: null,
    attributes: {
      namespace: ns,
      source: "synthetic",
      syntheticKind: "namespaceService",
    },
    position: null,
    size: null,
  }));

  const serviceByNamespace = new Map(serviceNodes.map((node) => [(node.attributes as any)?.namespace, node.id]));
  const serviceNodeIds = new Set(serviceNodes.map((node) => node.id));

  const runsOnByPod = new Map<string, string[]>();
  edges.forEach((edge) => {
    if (String(edge.relationType).trim().toLowerCase() !== "runs_on") {
      return;
    }
    const list = runsOnByPod.get(edge.source) ?? [];
    list.push(edge.target);
    runsOnByPod.set(edge.source, list);
  });

  const servicePodEdges: RawTopologyCanvasEdge[] = [];
  podNodes.forEach((pod) => {
    const ns = (pod.attributes as any)?.namespace;
    const serviceId = typeof ns === "string" ? serviceByNamespace.get(ns) : undefined;
    if (!serviceId) {
      return;
    }

    servicePodEdges.push({
      id: `edge-svc-ns-${serviceId}-${pod.id}`,
      source: serviceId,
      target: pod.id,
      relationType: "depends_on",
      status: "healthy",
      impactLevel: "low",
      label: "serves",
      isCritical: false,
      isAggregated: false,
    });
  });

  const serviceNodeEdges = new Map<string, RawTopologyCanvasEdge>();
  podNodes.forEach((pod) => {
    const ns = (pod.attributes as any)?.namespace;
    const serviceId = typeof ns === "string" ? serviceByNamespace.get(ns) : undefined;
    if (!serviceId) {
      return;
    }
    const hosts = runsOnByPod.get(pod.id) ?? [];
    hosts.forEach((hostId) => {
      const key = `${serviceId}::${hostId}`;
      if (serviceNodeEdges.has(key)) {
        return;
      }
      serviceNodeEdges.set(key, {
        id: `edge-svc-host-${serviceId}-${hostId}`,
        source: serviceId,
        target: hostId,
        relationType: "runs_on",
        status: "healthy",
        impactLevel: "low",
        label: "hosted_on",
        isCritical: false,
        isAggregated: false,
      });
    });
  });

  const nextNodes = [...nodes, ...serviceNodes];
  const nextEdges = [...edges, ...servicePodEdges, ...Array.from(serviceNodeEdges.values())];

  return { nodes: nextNodes, edges: nextEdges };
}

function pruneDanglingSwitchPorts(nodes: RawTopologyCanvasNode[], edges: RawTopologyCanvasEdge[]) {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const portIds = new Set(
    nodes
      .filter((node) => String(node.type ?? "").trim().toLowerCase().includes("port"))
      .map((node) => node.id),
  );
  const switchIds = new Set(
    nodes
      .filter((node) => String(node.type ?? "").trim().toLowerCase() === "switch")
      .map((node) => node.id),
  );

  const degreeToNonSwitch = new Map<string, number>();
  edges.forEach((edge) => {
    if (!portIds.has(edge.source) && !portIds.has(edge.target)) {
      return;
    }

    const portId = portIds.has(edge.source) ? edge.source : edge.target;
    const otherId = portId === edge.source ? edge.target : edge.source;
    if (switchIds.has(otherId)) {
      return;
    }

    const otherNode = nodeById.get(otherId);
    const otherType = String(otherNode?.type ?? "").trim().toLowerCase();
    if (otherType === "switch" || otherType === "switch_port") {
      return;
    }

    degreeToNonSwitch.set(portId, (degreeToNonSwitch.get(portId) ?? 0) + 1);
  });

  const danglingPortIds = Array.from(portIds).filter((portId) => (degreeToNonSwitch.get(portId) ?? 0) === 0);
  if (danglingPortIds.length === 0) {
    return { nodes, edges };
  }

  const danglingSet = new Set(danglingPortIds);
  const nextNodes = nodes.filter((node) => !danglingSet.has(node.id));
  const nextEdges = edges.filter((edge) => !danglingSet.has(edge.source) && !danglingSet.has(edge.target));
  return { nodes: nextNodes, edges: nextEdges };
}

const withPortSwitch = ensurePortSwitchEdges(canonicalRawNodes, canonicalRawEdges);
const withClusterSwitch = ensureClusterSwitchEdge(canonicalRawNodes, withPortSwitch);
const withNamespaceServices = buildNamespaceServices(canonicalRawNodes, withClusterSwitch);
const prunedPorts = pruneDanglingSwitchPorts(withNamespaceServices.nodes, withNamespaceServices.edges);
const canonicalRawNodesWithServices = prunedPorts.nodes;
const canonicalRawEdgesWithClusterSwitch = prunedPorts.edges;
const lastUpdated =
  topologyCanvas.meta?.lastUpdated ??
  topologyCanvas.meta?.exportedAt ??
  new Date().toISOString();

const site: TopologySite = {
  id: "aidc-online-topology",
  name: pickMostCommon(
    canonicalRawNodesWithServices
      .filter((node) => node.type === "cluster")
      .map((node) => node.name),
    "AIDC Online Topology",
  ),
  region: pickMostCommon(
    canonicalRawNodesWithServices.map((node) => node.region),
    "AIDC-CN",
  ),
  zone: pickMostCommon(
    canonicalRawNodesWithServices.map((node) => node.zone),
    "zone-a",
  ),
  domain: pickMostCommon(
    canonicalRawNodesWithServices.map((node) => node.domain),
    "aidc",
  ),
  summary: `基于 ${canonicalRawNodesWithServices.length} 个节点和 ${canonicalRawEdgesWithClusterSwitch.length} 条关系生成的线上拓扑 mock。`,
};

const nodes: TopologyObject[] = canonicalRawNodesWithServices.map((node) => ({
  id: node.id,
  name: node.name ?? node.id,
  type: mapNodeType(node),
  status: mapStatus(node.status),
  layer: node.layer,
  domain: node.domain ?? site.domain,
  region: node.region ?? site.region,
  zone: node.zone ?? site.zone,
  cluster: node.cluster ?? undefined,
  rack: node.rack ?? undefined,
  slot: node.slot ?? undefined,
  summary: node.summary ?? `${node.name ?? node.id} (${node.type})`,
  tags: node.tags ?? [],
  updatedAt: lastUpdated,
  metrics: node.metrics ?? undefined,
  attributes: {
    rawType: node.type,
    ...(node.attributes ?? {}),
    ...(node.position ? { canvasPosition: node.position } : {}),
    ...(node.size ? { canvasSize: node.size } : {}),
  },
}));

const edges: TopologyRelation[] = canonicalRawEdgesWithClusterSwitch.map((edge) => ({
  id: edge.id,
  source: edge.source,
  target: edge.target,
  relationType: mapRelationType(edge.relationType),
  status: mapStatus(edge.status),
  impactLevel: mapImpactLevel(edge.impactLevel),
  label: edge.label ?? edge.relationType,
  isCritical: Boolean(edge.isCritical),
  isAggregated: Boolean(edge.isAggregated),
}));

export const topologyExplorerOnlineMock: TopologyExplorerResponse = {
  site,
  nodes,
  edges,
  paths: [],
  lastUpdated,
};
