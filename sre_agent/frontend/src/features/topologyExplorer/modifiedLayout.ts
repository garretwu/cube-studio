import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3";

import type { TopologyObject, TopologyRelation } from "../../api/types";
import type { TopologyCanvasMetrics } from "./canvasConfig";
import type { ExplorerLayoutPreset } from "./types";

type HybridLayoutNode = SimulationNodeDatum & {
  id: string;
  node: TopologyObject;
  radius: number;
  targetX: number;
  targetY: number;
};

type HybridLayoutLink = SimulationLinkDatum<HybridLayoutNode> & {
  source: string | HybridLayoutNode;
  target: string | HybridLayoutNode;
  strength: number;
  distance: number;
};

type LayoutGroup = {
  key: string;
  nodes: TopologyObject[];
};

const STATUS_PRIORITY: Record<TopologyObject["status"], number> = {
  abnormal: 4,
  impacted: 3,
  maintenance: 2,
  healthy: 1,
};

const LAYER_ORDER: Record<TopologyObject["layer"], number> = {
  physical: 0,
  network: 1,
  compute: 2,
  service: 3,
};

function getRawType(node: TopologyObject) {
  return String(node.attributes.rawType ?? node.type);
}

function getAggregateCount(node: TopologyObject) {
  const aggregateCount = Number(node.attributes.aggregateCount ?? 0);
  return Number.isFinite(aggregateCount) ? aggregateCount : 0;
}

function getNodeRadius(node: TopologyObject, metrics: TopologyCanvasMetrics) {
  const aggregateCount = getAggregateCount(node);
  const radiusBase = Math.max(metrics.nodeHeight, metrics.nodeWidth) * 0.34;

  if (aggregateCount > 0) {
    return radiusBase + Math.min(aggregateCount, 18) * 1.4;
  }

  if (node.type === "cluster") {
    return radiusBase + 18;
  }

  if (node.type === "service") {
    return radiusBase - 6;
  }

  return radiusBase;
}

function getStructuralEdges(edges: TopologyRelation[]) {
  return edges.filter((edge) => edge.relationType !== "depends_on");
}

function getPrimaryHostId(node: TopologyObject, outgoingEdges: Map<string, TopologyRelation[]>) {
  const hostEdge = (outgoingEdges.get(node.id) ?? []).find((edge) => edge.relationType === "runs_on");
  return hostEdge?.target ?? node.cluster ?? node.rack ?? node.domain;
}

function getGroupKey(
  node: TopologyObject,
  incomingEdges: Map<string, TopologyRelation[]>,
  outgoingEdges: Map<string, TopologyRelation[]>,
) {
  if (node.layer === "service") {
    return `service:${getPrimaryHostId(node, outgoingEdges)}:${getRawType(node)}`;
  }

  if (node.layer === "compute") {
    return `compute:${node.rack ?? (incomingEdges.get(node.id) ?? [])[0]?.source ?? node.cluster ?? node.id}`;
  }

  if (node.layer === "network") {
    return `network:${node.zone ?? node.domain ?? node.id}`;
  }

  return `physical:${node.cluster ?? node.region ?? node.id}`;
}

function getGroupedEdges(edges: TopologyRelation[]) {
  const incoming = new Map<string, TopologyRelation[]>();
  const outgoing = new Map<string, TopologyRelation[]>();

  edges.forEach((edge) => {
    const incomingList = incoming.get(edge.target) ?? [];
    incomingList.push(edge);
    incoming.set(edge.target, incomingList);

    const outgoingList = outgoing.get(edge.source) ?? [];
    outgoingList.push(edge);
    outgoing.set(edge.source, outgoingList);
  });

  return { incoming, outgoing };
}

function compareNodes(
  left: TopologyObject,
  right: TopologyObject,
  incomingEdges: Map<string, TopologyRelation[]>,
  outgoingEdges: Map<string, TopologyRelation[]>,
) {
  const leftPriority = STATUS_PRIORITY[left.status] ?? 0;
  const rightPriority = STATUS_PRIORITY[right.status] ?? 0;
  if (leftPriority !== rightPriority) {
    return rightPriority - leftPriority;
  }

  const leftAggregateCount = getAggregateCount(left);
  const rightAggregateCount = getAggregateCount(right);
  if (leftAggregateCount !== rightAggregateCount) {
    return rightAggregateCount - leftAggregateCount;
  }

  const leftDegree = (incomingEdges.get(left.id)?.length ?? 0) + (outgoingEdges.get(left.id)?.length ?? 0);
  const rightDegree = (incomingEdges.get(right.id)?.length ?? 0) + (outgoingEdges.get(right.id)?.length ?? 0);
  if (leftDegree !== rightDegree) {
    return rightDegree - leftDegree;
  }

  return left.name.localeCompare(right.name, "zh-Hans-CN");
}

function getCenteredOffset(index: number, count: number, gap: number) {
  return count <= 1 ? 0 : (index - (count - 1) / 2) * gap;
}

function getNetworkFamilyKey(node: TopologyObject) {
  const delimiterIndex = node.id.indexOf(":");
  return delimiterIndex > 0 ? node.id.slice(0, delimiterIndex) : node.id;
}

function getComputeFamilyKey(node: TopologyObject) {
  const host = typeof node.attributes.host === "string" ? node.attributes.host : undefined;
  if (host) {
    return host;
  }

  if (node.id.startsWith("bmc:")) {
    return node.id.slice(4);
  }

  if (node.id.startsWith("gpu:")) {
    const [, family] = node.id.split(":");
    return family ?? node.id;
  }

  return node.id;
}

function getLayerGroupLeader(
  group: LayoutGroup,
  incomingEdges: Map<string, TopologyRelation[]>,
  outgoingEdges: Map<string, TopologyRelation[]>,
) {
  const sorted = [...group.nodes].sort((left, right) => compareNodes(left, right, incomingEdges, outgoingEdges));

  if (group.nodes[0]?.layer === "network") {
    return (
      group.nodes.find((node) => node.id === group.key) ??
      group.nodes.find((node) => !node.id.includes(":")) ??
      sorted[0]
    );
  }

  if (group.nodes[0]?.layer === "compute") {
    return (
      group.nodes.find((node) => node.type === "node" && node.id === group.key) ??
      group.nodes.find((node) => node.type === "node" && !node.id.startsWith("bmc:")) ??
      sorted[0]
    );
  }

  return sorted[0];
}

function sortGroupNodes(
  group: LayoutGroup,
  incomingEdges: Map<string, TopologyRelation[]>,
  outgoingEdges: Map<string, TopologyRelation[]>,
) {
  const leader = getLayerGroupLeader(group, incomingEdges, outgoingEdges);
  const rest = group.nodes
    .filter((node) => node.id !== leader?.id)
    .sort((left, right) => compareNodes(left, right, incomingEdges, outgoingEdges));

  return leader ? [leader, ...rest] : rest;
}

function getGroupTileColumns(layer: TopologyObject["layer"], size: number) {
  if (layer === "service") {
    return size >= 7 ? 3 : size >= 3 ? 2 : 1;
  }

  if (layer === "compute") {
    return size >= 4 ? 2 : 1;
  }

  if (layer === "network") {
    return size >= 5 ? 2 : 1;
  }

  return 1;
}

function createLayoutGroups(nodes: TopologyObject[], keyResolver: (node: TopologyObject) => string) {
  const groups = new Map<string, TopologyObject[]>();

  nodes.forEach((node) => {
    const key = keyResolver(node);
    const list = groups.get(key) ?? [];
    list.push(node);
    groups.set(key, list);
  });

  return Array.from(groups.entries()).map(([key, groupedNodes]) => ({ key, nodes: groupedNodes }));
}

function placeGroupedLayer(
  positions: Map<string, { x: number; y: number }>,
  groups: LayoutGroup[],
  options: {
    baseX: number;
    startY: number;
    groupColumns: number;
    groupXGap: number;
    groupRowGap: number;
    localXGap: number;
    localYGap: number;
    incomingEdges: Map<string, TopologyRelation[]>;
    outgoingEdges: Map<string, TopologyRelation[]>;
  },
) {
  const {
    baseX,
    startY,
    groupColumns,
    groupXGap,
    groupRowGap,
    localXGap,
    localYGap,
    incomingEdges,
    outgoingEdges,
  } = options;

  let cursorY = startY;

  for (let offset = 0; offset < groups.length; offset += groupColumns) {
    const rowGroups = groups.slice(offset, offset + groupColumns);
    const tileHeights = rowGroups.map((group) => {
      const orderedNodes = sortGroupNodes(group, incomingEdges, outgoingEdges);
      const localColumns = getGroupTileColumns(orderedNodes[0]?.layer ?? "service", orderedNodes.length);
      const rowCount = Math.max(1, Math.ceil(orderedNodes.length / localColumns));
      return Math.max(localYGap * Math.max(0, rowCount - 1), 0);
    });
    const rowHeight = Math.max(...tileHeights, 0);

    rowGroups.forEach((group, groupIndex) => {
      const orderedNodes = sortGroupNodes(group, incomingEdges, outgoingEdges);
      const localColumns = getGroupTileColumns(orderedNodes[0]?.layer ?? "service", orderedNodes.length);
      const rowCount = Math.max(1, Math.ceil(orderedNodes.length / localColumns));
      const tileHeight = Math.max(localYGap * Math.max(0, rowCount - 1), 0);
      const anchorX = baseX + getCenteredOffset(groupIndex, groupColumns, groupXGap);
      const anchorY = cursorY + (rowHeight - tileHeight) / 2;

      orderedNodes.forEach((node, nodeIndex) => {
        const localColumn = nodeIndex % localColumns;
        const localRow = Math.floor(nodeIndex / localColumns);
        positions.set(node.id, {
          x: Math.round(anchorX + getCenteredOffset(localColumn, localColumns, localXGap)),
          y: Math.round(anchorY + localRow * localYGap),
        });
      });
    });

    cursorY += rowHeight + groupRowGap;
  }

  return cursorY;
}

function createLayeredTargets(
  nodes: TopologyObject[],
  metrics: TopologyCanvasMetrics,
  incomingEdges: Map<string, TopologyRelation[]>,
  outgoingEdges: Map<string, TopologyRelation[]>,
) {
  const positions = new Map<string, { x: number; y: number }>();
  const physicalNodes = nodes
    .filter((node) => node.layer === "physical")
    .sort((left, right) => compareNodes(left, right, incomingEdges, outgoingEdges));
  const networkGroups = createLayoutGroups(
    nodes.filter((node) => node.layer === "network"),
    getNetworkFamilyKey,
  );
  const computeGroups = createLayoutGroups(
    nodes.filter((node) => node.layer === "compute"),
    getComputeFamilyKey,
  );
  const serviceGroups = createLayoutGroups(
    nodes.filter((node) => node.layer === "service"),
    (node) => String(getPrimaryHostId(node, outgoingEdges) ?? "unassigned"),
  );

  const serviceCountsByHost = new Map(serviceGroups.map((group) => [group.key, group.nodes.length]));

  networkGroups.sort((left, right) => {
    const leftLeader = getLayerGroupLeader(left, incomingEdges, outgoingEdges);
    const rightLeader = getLayerGroupLeader(right, incomingEdges, outgoingEdges);
    return compareNodes(leftLeader, rightLeader, incomingEdges, outgoingEdges);
  });

  computeGroups.sort((left, right) => {
    const serviceDelta = (serviceCountsByHost.get(right.key) ?? 0) - (serviceCountsByHost.get(left.key) ?? 0);
    if (serviceDelta !== 0) {
      return serviceDelta;
    }

    const leftLeader = getLayerGroupLeader(left, incomingEdges, outgoingEdges);
    const rightLeader = getLayerGroupLeader(right, incomingEdges, outgoingEdges);
    return compareNodes(leftLeader, rightLeader, incomingEdges, outgoingEdges);
  });

  const computeGroupOrder = new Map(computeGroups.map((group, index) => [group.key, index]));
  serviceGroups.sort((left, right) => {
    const leftOrder = computeGroupOrder.get(left.key);
    const rightOrder = computeGroupOrder.get(right.key);

    if (leftOrder !== undefined && rightOrder !== undefined && leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }

    if (leftOrder !== undefined && rightOrder === undefined) {
      return -1;
    }

    if (leftOrder === undefined && rightOrder !== undefined) {
      return 1;
    }

    const sizeDelta = right.nodes.length - left.nodes.length;
    if (sizeDelta !== 0) {
      return sizeDelta;
    }

    const leftLeader = getLayerGroupLeader(left, incomingEdges, outgoingEdges);
    const rightLeader = getLayerGroupLeader(right, incomingEdges, outgoingEdges);
    return compareNodes(leftLeader, rightLeader, incomingEdges, outgoingEdges);
  });

  const physicalX = metrics.layerXOffset;
  const networkX = metrics.layerXOffset + LAYER_ORDER.network * metrics.layerXSpacing;
  const computeX = metrics.layerXOffset + LAYER_ORDER.compute * metrics.layerXSpacing;
  const serviceX = metrics.layerXOffset + LAYER_ORDER.service * metrics.layerXSpacing + Math.round(metrics.nodeWidth * 1.2);

  physicalNodes.forEach((node, index) => {
    positions.set(node.id, {
      x: Math.round(physicalX),
      y: Math.round(metrics.layerYOffset + index * Math.round(metrics.layerYSpacing * 1.18)),
    });
  });

  const networkBottomY = placeGroupedLayer(positions, networkGroups, {
    baseX: networkX,
    startY: metrics.layerYOffset,
    groupColumns: 1,
    groupXGap: Math.round(metrics.nodeWidth * 0.8),
    groupRowGap: Math.round(metrics.nodeHeight * 1.04),
    localXGap: Math.round(metrics.nodeWidth * 0.9),
    localYGap: Math.round(metrics.nodeHeight * 0.9),
    incomingEdges,
    outgoingEdges,
  });

  const computeBottomY = placeGroupedLayer(positions, computeGroups, {
    baseX: computeX,
    startY: metrics.layerYOffset,
    groupColumns: 2,
    groupXGap: Math.round(metrics.nodeWidth * 1.5),
    groupRowGap: Math.round(metrics.nodeHeight * 1.12),
    localXGap: Math.round(metrics.nodeWidth * 0.88),
    localYGap: Math.round(metrics.nodeHeight * 0.94),
    incomingEdges,
    outgoingEdges,
  });

  placeGroupedLayer(positions, serviceGroups, {
    baseX: serviceX,
    startY: metrics.layerYOffset,
    groupColumns: 3,
    groupXGap: Math.round(metrics.nodeWidth * 1.45),
    groupRowGap: Math.round(metrics.nodeHeight * 1.1),
    localXGap: Math.round(metrics.nodeWidth * 0.84),
    localYGap: Math.round(metrics.nodeHeight * 0.9),
    incomingEdges,
    outgoingEdges,
  });

  if (!physicalNodes.length) {
    return positions;
  }

  const contentBottom = Math.max(networkBottomY, computeBottomY, metrics.layerYOffset + Math.round(metrics.nodeHeight * 1.4));
  const centeredClusterY = Math.max(metrics.layerYOffset + 48, Math.round(contentBottom / 2.8));
  physicalNodes.forEach((node, index) => {
    positions.set(node.id, {
      x: Math.round(physicalX),
      y: Math.round(centeredClusterY + index * Math.round(metrics.layerYSpacing * 0.82)),
    });
  });

  return positions;
}

function createDomainTargets(
  nodes: TopologyObject[],
  metrics: TopologyCanvasMetrics,
  incomingEdges: Map<string, TopologyRelation[]>,
  outgoingEdges: Map<string, TopologyRelation[]>,
) {
  const positions = new Map<string, { x: number; y: number }>();
  const perType = new Map<TopologyObject["type"], TopologyObject[]>();

  nodes.forEach((node) => {
    const list = perType.get(node.type) ?? [];
    list.push(node);
    perType.set(node.type, list);
  });

  (Object.keys(metrics.typeOrder) as Array<TopologyObject["type"]>).forEach((type) => {
    const anchor = metrics.typeOrder[type];
    const typeNodes = [...(perType.get(type) ?? [])].sort((left, right) => {
      const leftGroup = getGroupKey(left, incomingEdges, outgoingEdges);
      const rightGroup = getGroupKey(right, incomingEdges, outgoingEdges);
      if (leftGroup !== rightGroup) {
        return leftGroup.localeCompare(rightGroup, "zh-Hans-CN");
      }

      return compareNodes(left, right, incomingEdges, outgoingEdges);
    });

    const columnCount =
      type === "service"
        ? Math.min(4, Math.max(2, Math.ceil(Math.sqrt(typeNodes.length / 1.8))))
        : type === "node" || type === "switch" || type === "port" || type === "bmc"
          ? Math.min(3, Math.max(1, Math.ceil(Math.sqrt(typeNodes.length / 2))))
          : Math.min(2, Math.max(1, Math.ceil(Math.sqrt(typeNodes.length))));
    const columnGap = Math.round(metrics.nodeWidth * 0.9);
    const rowGap = Math.round(metrics.nodeHeight * 1.12);

    typeNodes.forEach((node, index) => {
      const columnIndex = index % columnCount;
      const rowIndex = Math.floor(index / columnCount);
      const centeredColumn = columnCount === 1 ? 0 : columnIndex - (columnCount - 1) / 2;
      positions.set(node.id, {
        x: anchor.x + metrics.typeXOffset + centeredColumn * columnGap,
        y: anchor.y + metrics.typeYOffset + rowIndex * rowGap,
      });
    });
  });

  return positions;
}

export function computeModifiedHybridLayout(
  nodes: TopologyObject[],
  edges: TopologyRelation[],
  layoutPreset: ExplorerLayoutPreset,
  metrics: TopologyCanvasMetrics,
) {
  const positions = new Map<string, { x: number; y: number }>();
  if (nodes.length === 0) {
    return positions;
  }

  const structuralEdges = getStructuralEdges(edges);
  const { incoming, outgoing } = getGroupedEdges(edges);
  const baseTargets =
    layoutPreset === "layered"
      ? createLayeredTargets(nodes, metrics, incoming, outgoing)
      : createDomainTargets(nodes, metrics, incoming, outgoing);

  if (layoutPreset === "layered") {
    const minX = 32;
    const minY = 28;
    baseTargets.forEach((target, nodeId) => {
      positions.set(nodeId, {
        x: Math.max(minX, Math.round(target.x)),
        y: Math.max(minY, Math.round(target.y)),
      });
    });
    return positions;
  }

  const layoutNodes: HybridLayoutNode[] = nodes.map((node) => {
    const target = baseTargets.get(node.id) ?? { x: metrics.layerXOffset, y: metrics.layerYOffset };
    const radius = getNodeRadius(node, metrics);
    return {
      id: node.id,
      node,
      radius,
      targetX: target.x,
      targetY: target.y,
      x: target.x,
      y: target.y,
      vx: 0,
      vy: 0,
    };
  });

  const layoutLinks: HybridLayoutLink[] = structuralEdges
    .filter((edge) => baseTargets.has(edge.source) && baseTargets.has(edge.target))
    .map((edge) => ({
      source: edge.source,
      target: edge.target,
      strength: edge.isCritical ? 0.14 : edge.isAggregated ? 0.05 : 0.08,
      distance: edge.isAggregated ? metrics.nodeWidth * 1.38 : metrics.nodeWidth * 1.18,
    }));

  const simulation = forceSimulation(layoutNodes)
    .alpha(0.74)
    .alphaDecay(0.12)
    .velocityDecay(0.5)
    .force(
      "link",
      forceLink<HybridLayoutNode, HybridLayoutLink>(layoutLinks)
        .id((linkNode) => linkNode.id)
        .strength((link) => link.strength)
        .distance((link) => link.distance),
    )
    .force("charge", forceManyBody<HybridLayoutNode>().strength((node) => -Math.max(18, node.radius * 1.4)))
    .force("x", forceX<HybridLayoutNode>((node) => node.targetX).strength(0.28))
    .force("y", forceY<HybridLayoutNode>((node) => node.targetY).strength(0.24))
    .force("collide", forceCollide<HybridLayoutNode>().radius((node) => node.radius).iterations(3));

  for (let tick = 0; tick < 110; tick += 1) {
    simulation.tick();
  }
  simulation.stop();

  const minX = 32;
  const minY = 28;
  layoutNodes.forEach((layoutNode) => {
    positions.set(layoutNode.id, {
      x: Math.max(minX, Math.round(layoutNode.x ?? layoutNode.targetX)),
      y: Math.max(minY, Math.round(layoutNode.y ?? layoutNode.targetY)),
    });
  });

  return positions;
}

