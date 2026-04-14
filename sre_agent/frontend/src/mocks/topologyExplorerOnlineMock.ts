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
  if (normalized === "pod" || normalized === "service") {
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
const lastUpdated =
  topologyCanvas.meta?.lastUpdated ??
  topologyCanvas.meta?.exportedAt ??
  new Date().toISOString();

const site: TopologySite = {
  id: "aidc-online-topology",
  name: pickMostCommon(
    rawNodes
      .filter((node) => node.type === "cluster")
      .map((node) => node.name),
    "AIDC Online Topology",
  ),
  region: pickMostCommon(
    rawNodes.map((node) => node.region),
    "AIDC-CN",
  ),
  zone: pickMostCommon(
    rawNodes.map((node) => node.zone),
    "zone-a",
  ),
  domain: pickMostCommon(
    rawNodes.map((node) => node.domain),
    "aidc",
  ),
  summary: `基于 ${rawNodes.length} 个节点和 ${rawEdges.length} 条关系生成的线上拓扑 mock。`,
};

const nodes: TopologyObject[] = rawNodes.map((node) => ({
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

const edges: TopologyRelation[] = rawEdges.map((edge) => ({
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
