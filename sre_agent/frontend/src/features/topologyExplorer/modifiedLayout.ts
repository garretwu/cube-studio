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
  const aggregateHostId =
    typeof node.attributes.aggregateHostId === "string" ? node.attributes.aggregateHostId : undefined;
  if (aggregateHostId) {
    const serviceHostEdge = (outgoingEdges.get(aggregateHostId) ?? []).find(
      (edge) => edge.relationType === "runs_on",
    );
    if (serviceHostEdge?.target) {
      return serviceHostEdge.target;
    }
  }
  const hostEdge = (outgoingEdges.get(node.id) ?? []).find((edge) => edge.relationType === "runs_on");
  return hostEdge?.target ?? node.cluster ?? node.rack ?? node.domain;
}

function getGroupKey(
  node: TopologyObject,
  incomingEdges: Map<string, TopologyRelation[]>,
  outgoingEdges: Map<string, TopologyRelation[]>,
) {
  if (node.layer === "service") {
    // Keep namespace services and their pod groups close to their compute hosts.
    return `service:${getPrimaryHostId(node, outgoingEdges)}:${node.type}`;
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

type AnchoredLaneItem = {
  id: string;
  anchorY: number;
  tieBreaker: string;
};

type ExpandedLaneItem = AnchoredLaneItem & {
  preferredX?: number;
};

function placeLaneByAnchors(items: AnchoredLaneItem[], minGap: number) {
  const normalizedGap = Math.max(1, Math.round(minGap));
  const sorted = [...items].sort((left, right) => {
    if (left.anchorY !== right.anchorY) {
      return left.anchorY - right.anchorY;
    }
    return left.tieBreaker.localeCompare(right.tieBreaker, "zh-Hans-CN");
  });

  const placed = sorted.map((item) => ({
    ...item,
    y: Math.round(item.anchorY),
  }));

  for (let index = 1; index < placed.length; index += 1) {
    const minY = placed[index - 1].y + normalizedGap;
    if (placed[index].y < minY) {
      placed[index].y = minY;
    }
  }

  for (let index = placed.length - 2; index >= 0; index -= 1) {
    const maxY = placed[index + 1].y - normalizedGap;
    if (placed[index].y > maxY) {
      placed[index].y = maxY;
    }
  }

  for (let index = 1; index < placed.length; index += 1) {
    const minY = placed[index - 1].y + normalizedGap;
    if (placed[index].y < minY) {
      placed[index].y = minY;
    }
  }

  return new Map(placed.map((item) => [item.id, item.y]));
}

function placeExpandedLane(
  items: ExpandedLaneItem[],
  options: {
    startX: number;
    startY: number;
    rowGap: number;
    columnGap: number;
    maxRows: number;
  },
) {
  const { startX, startY, rowGap, columnGap, maxRows } = options;
  const normalizedMaxRows = Math.max(1, Math.round(maxRows));
  const sorted = [...items].sort((left, right) => {
    if (left.anchorY !== right.anchorY) {
      return left.anchorY - right.anchorY;
    }
    return left.tieBreaker.localeCompare(right.tieBreaker, "zh-Hans-CN");
  });

  return new Map(
    sorted.map((item, index) => {
      const columnIndex = Math.floor(index / normalizedMaxRows);
      const rowIndex = index % normalizedMaxRows;
      return [
        item.id,
        {
          x: Math.round((item.preferredX ?? startX) + columnIndex * columnGap),
          y: Math.round(startY + rowIndex * rowGap),
        },
      ];
    }),
  );
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

  if (node.id.startsWith("aggregate-bmc:")) {
    return node.id.slice("aggregate-bmc:".length);
  }

  if (node.id.startsWith("aggregate-gpu:")) {
    const [, family] = node.id.split(":");
    return family ?? node.id;
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
    if (size >= 30) {
      return 10;
    }
    if (size >= 20) {
      return 8;
    }
    if (size >= 14) {
      return 6;
    }
    if (size >= 9) {
      return 5;
    }
    return size >= 7 ? 4 : size >= 3 ? 3 : 1;
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
  const nodeById = new Map(nodes.map((node) => [node.id, node]));

  const byType = (type: TopologyObject["type"]) =>
    nodes
      .filter((node) => node.type === type)
      .sort((left, right) => compareNodes(left, right, incomingEdges, outgoingEdges));

  const clusters = byType("cluster");
  const racks = byType("rack");
  const switches = byType("switch");
  const ports = byType("port");
  const workers = byType("node");
  const bmcs = byType("bmc");
  const gpus = byType("gpu");
  const services = byType("service");
  const pods = byType("pod");

  const columnStep = Math.round(metrics.nodeWidth * 1.42);
  const clusterX = Math.round(metrics.layerXOffset);
  const switchX = clusterX + columnStep;
  const portX = switchX + columnStep;
  const nodeX = portX + columnStep;
  const branchX = nodeX + Math.round(columnStep * 0.62);
  const serviceX = nodeX + Math.round(columnStep * 1.84);
  const podX = serviceX + Math.round(columnStep * 1.42);
  const serviceBridgeX = nodeX + Math.round(columnStep * 1.26);

  const relationFallbackScore: Record<TopologyRelation["relationType"], number> = {
    contains: 0,
    runs_on: 1,
    connects_to: 2,
    uplink_to: 3,
    depends_on: 4,
    aggregated: 5,
  };

  const getNodeEdges = (nodeId: string) => [
    ...(outgoingEdges.get(nodeId) ?? []),
    ...(incomingEdges.get(nodeId) ?? []),
  ];

  const getRelatedIdsByType = (
    nodeId: string,
    targetType: TopologyObject["type"],
    preferredRelations: TopologyRelation["relationType"][] = [],
  ) => {
    const rankByRelation = new Map(preferredRelations.map((relationType, index) => [relationType, index]));
    const bestById = new Map<string, number>();

    getNodeEdges(nodeId).forEach((edge) => {
      const neighborId = edge.source === nodeId ? edge.target : edge.source;
      const neighbor = nodeById.get(neighborId);
      if (!neighbor || neighbor.type !== targetType) {
        return;
      }

      const preferredRank = rankByRelation.get(edge.relationType);
      const score =
        preferredRank !== undefined
          ? preferredRank
          : preferredRelations.length + (relationFallbackScore[edge.relationType] ?? 99);
      const previous = bestById.get(neighborId);
      if (previous === undefined || score < previous) {
        bestById.set(neighborId, score);
      }
    });

    return Array.from(bestById.entries())
      .sort((left, right) => {
        if (left[1] !== right[1]) {
          return left[1] - right[1];
        }
        const leftNode = nodeById.get(left[0]);
        const rightNode = nodeById.get(right[0]);
        if (!leftNode || !rightNode) {
          return left[0].localeCompare(right[0], "zh-Hans-CN");
        }
        return compareNodes(leftNode, rightNode, incomingEdges, outgoingEdges);
      })
      .map(([id]) => id);
  };

  const getMaxPlacedY = () => {
    const all = Array.from(positions.values());
    if (all.length === 0) {
      return metrics.layerYOffset;
    }
    return Math.max(...all.map((point) => point.y));
  };

  const orderedSwitches = [...switches].sort((left, right) => left.name.localeCompare(right.name, "zh-Hans-CN"));
  const switchIdSet = new Set(orderedSwitches.map((node) => node.id));

  const resolvePortSwitchId = (port: TopologyObject) => {
    const connectedSwitch = getRelatedIdsByType(port.id, "switch", ["contains", "uplink_to", "connects_to"])[0];
    if (connectedSwitch) {
      return connectedSwitch;
    }

    if (port.id.includes(":")) {
      const [prefix] = port.id.split(":");
      if (prefix && switchIdSet.has(prefix)) {
        return prefix;
      }
    }

    return orderedSwitches[0]?.id;
  };

  const orderedPorts = [...ports].sort((left, right) => {
    const leftSwitch = resolvePortSwitchId(left) ?? "";
    const rightSwitch = resolvePortSwitchId(right) ?? "";
    const leftSwitchOrder = orderedSwitches.findIndex((item) => item.id === leftSwitch);
    const rightSwitchOrder = orderedSwitches.findIndex((item) => item.id === rightSwitch);

    const normalizedLeftOrder = leftSwitchOrder === -1 ? Number.MAX_SAFE_INTEGER : leftSwitchOrder;
    const normalizedRightOrder = rightSwitchOrder === -1 ? Number.MAX_SAFE_INTEGER : rightSwitchOrder;
    if (normalizedLeftOrder !== normalizedRightOrder) {
      return normalizedLeftOrder - normalizedRightOrder;
    }

    return left.name.localeCompare(right.name, "zh-Hans-CN");
  });

  const portGap = Math.round(metrics.nodeHeight * 1.04);
  const portTopY = Math.round(metrics.layerYOffset + metrics.nodeHeight * 0.6);
  const portYById = new Map<string, number>();
  orderedPorts.forEach((port, index) => {
    const y = Math.round(portTopY + index * portGap);
    portYById.set(port.id, y);
    positions.set(port.id, { x: portX, y });
  });

  const switchCenterY =
    orderedPorts.length > 0
      ? Math.round(portTopY + ((orderedPorts.length - 1) * portGap) / 2)
      : Math.round(metrics.layerYOffset + metrics.nodeHeight * 2.2);

  orderedSwitches.forEach((switchNode, index) => {
    const relatedPortYs = orderedPorts
      .filter((port) => resolvePortSwitchId(port) === switchNode.id)
      .map((port) => portYById.get(port.id))
      .filter((value): value is number => value !== undefined);

    const fallbackY = Math.round(
      switchCenterY +
        getCenteredOffset(index, Math.max(1, orderedSwitches.length), Math.round(metrics.nodeHeight * 1.2)),
    );

    const y =
      relatedPortYs.length > 0
        ? Math.round(relatedPortYs.reduce((sum, value) => sum + value, 0) / relatedPortYs.length)
        : fallbackY;

    positions.set(switchNode.id, { x: switchX, y });
  });

  const orderedClusters = [...clusters].sort((left, right) => left.name.localeCompare(right.name, "zh-Hans-CN"));
  orderedClusters.forEach((clusterNode, index) => {
    const y = Math.round(
      switchCenterY +
        getCenteredOffset(index, Math.max(1, orderedClusters.length), Math.round(metrics.nodeHeight * 1.08)),
    );
    positions.set(clusterNode.id, { x: clusterX, y });
  });

  const workerPortBinding = new Map<string, string>();
  const workersByPort = new Map<string, TopologyObject[]>();
  const unboundWorkers: TopologyObject[] = [];
  const portOrder = new Map(orderedPorts.map((port, index) => [port.id, index]));

  workers.forEach((worker) => {
    const connectedPorts = getRelatedIdsByType(worker.id, "port", ["contains", "connects_to", "uplink_to", "runs_on"]);
    const bestPortId = connectedPorts
      .filter((portId) => portOrder.has(portId))
      .sort((left, right) => (portOrder.get(left) ?? 0) - (portOrder.get(right) ?? 0))[0];

    if (!bestPortId) {
      unboundWorkers.push(worker);
      return;
    }

    workerPortBinding.set(worker.id, bestPortId);
    const list = workersByPort.get(bestPortId) ?? [];
    list.push(worker);
    workersByPort.set(bestPortId, list);
  });

  const workerYById = new Map<string, number>();
  const workerStackGap = Math.round(metrics.nodeHeight * 0.74);

  orderedPorts.forEach((port) => {
    const anchorY = portYById.get(port.id) ?? switchCenterY;
    const members = [...(workersByPort.get(port.id) ?? [])].sort((left, right) =>
      compareNodes(left, right, incomingEdges, outgoingEdges),
    );

    members.forEach((worker, index) => {
      const y = Math.round(anchorY + getCenteredOffset(index, members.length, workerStackGap));
      workerYById.set(worker.id, y);
      positions.set(worker.id, { x: nodeX, y });
    });
  });

  let unboundWorkerCursorY =
    orderedPorts.length > 0
      ? Math.round((portYById.get(orderedPorts[orderedPorts.length - 1].id) ?? switchCenterY) + workerStackGap * 1.3)
      : Math.round(switchCenterY + workerStackGap);

  [...unboundWorkers]
    .sort((left, right) => compareNodes(left, right, incomingEdges, outgoingEdges))
    .forEach((worker) => {
      workerYById.set(worker.id, unboundWorkerCursorY);
      positions.set(worker.id, { x: nodeX, y: unboundWorkerCursorY });
      unboundWorkerCursorY += workerStackGap;
    });

  let rackCursorY = Math.max(
    getMaxPlacedY() + Math.round(metrics.nodeHeight * 0.6),
    Math.round(switchCenterY + metrics.nodeHeight * 1.4),
  );
  racks.forEach((rack) => {
    positions.set(rack.id, { x: clusterX, y: rackCursorY });
    rackCursorY += Math.round(metrics.nodeHeight * 0.92);
  });

  const serviceHostById = new Map<string, string>();
  const servicesByHost = new Map<string, TopologyObject[]>();

  services.forEach((service) => {
    const hostId = getRelatedIdsByType(service.id, "node", ["runs_on", "contains", "depends_on"])[0];
    if (hostId) {
      serviceHostById.set(service.id, hostId);
    }

    const list = servicesByHost.get(hostId ?? "unassigned") ?? [];
    list.push(service);
    servicesByHost.set(hostId ?? "unassigned", list);
  });

  const serviceYById = new Map<string, number>();
  const serviceAnchorGap = Math.round(metrics.nodeHeight * 0.6);
  const serviceLaneGap = Math.round(metrics.nodeHeight * 0.78);
  let unassignedServiceAnchorY = Math.max(
    getMaxPlacedY() + Math.round(metrics.nodeHeight * 0.82),
    Math.round(switchCenterY + metrics.nodeHeight * 2.25),
  );

  const assignedHostIds = [...servicesByHost.keys()]
    .filter((hostId) => hostId !== "unassigned")
    .sort((left, right) => {
      const leftY = workerYById.get(left) ?? Number.MAX_SAFE_INTEGER;
      const rightY = workerYById.get(right) ?? Number.MAX_SAFE_INTEGER;
      if (leftY !== rightY) {
        return leftY - rightY;
      }
      return left.localeCompare(right, "zh-Hans-CN");
    });

  const serviceAnchors: AnchoredLaneItem[] = [];

  assignedHostIds.forEach((hostId) => {
    const hostY = workerYById.get(hostId);
    const serviceMembers = [...(servicesByHost.get(hostId) ?? [])].sort((left, right) =>
      compareNodes(left, right, incomingEdges, outgoingEdges),
    );

    if (hostY === undefined) {
      serviceMembers.forEach((service) => {
        serviceAnchors.push({
          id: service.id,
          anchorY: unassignedServiceAnchorY,
          tieBreaker: "service-unassigned-" + service.name + "-" + service.id,
        });
        unassignedServiceAnchorY += serviceLaneGap;
      });
      return;
    }

    serviceMembers.forEach((service, index) => {
      serviceAnchors.push({
        id: service.id,
        anchorY: hostY + getCenteredOffset(index, serviceMembers.length, serviceAnchorGap),
        tieBreaker: "service-" + hostY + "-" + index + "-" + service.name + "-" + service.id,
      });
    });
  });

  [...(servicesByHost.get("unassigned") ?? [])]
    .sort((left, right) => compareNodes(left, right, incomingEdges, outgoingEdges))
    .forEach((service, index) => {
      serviceAnchors.push({
        id: service.id,
        anchorY: unassignedServiceAnchorY,
        tieBreaker: "service-unassigned-tail-" + index + "-" + service.name + "-" + service.id,
      });
      unassignedServiceAnchorY += serviceLaneGap;
    });

  const serviceLaneYById = placeLaneByAnchors(serviceAnchors, serviceLaneGap);
  const serviceColumnGap = Math.round(metrics.nodeWidth * 0.92);
  const serviceFlowStartY = Math.round(metrics.layerYOffset + metrics.nodeHeight * 0.48);
  const serviceFlowPositions = placeExpandedLane(
    serviceAnchors.map((item) => {
      const serviceNode = nodeById.get(item.id);
      const hasComputeDependency =
        serviceNode?.type === "service" &&
        getRelatedIdsByType(serviceNode.id, "gpu", ["depends_on", "runs_on", "contains"]).length > 0;
      const hasDownstreamPods =
        serviceNode?.type === "service" &&
        getRelatedIdsByType(serviceNode.id, "pod", ["depends_on", "contains", "runs_on"]).length > 0;
      const isBridgeService = Boolean(hasComputeDependency && hasDownstreamPods);
      return {
        ...item,
        preferredX: isBridgeService ? serviceBridgeX : serviceX,
      };
    }),
    {
      startX: serviceX,
      startY: serviceFlowStartY,
      rowGap: serviceLaneGap,
      columnGap: serviceColumnGap,
      // Keep namespace service groups in a compact vertical lane near the host side.
      maxRows: Math.max(1, serviceAnchors.length),
    },
  );
  serviceAnchors.forEach((item) => {
    const fallbackY = serviceLaneYById.get(item.id) ?? Math.round(item.anchorY);
    const position = serviceFlowPositions.get(item.id) ?? { x: serviceX, y: fallbackY };
    const serviceNode = nodeById.get(item.id);
    const hasComputeDependency =
      serviceNode?.type === "service" &&
      getRelatedIdsByType(serviceNode.id, "gpu", ["depends_on", "runs_on", "contains"]).length > 0;
    const hasDownstreamPods =
      serviceNode?.type === "service" &&
      getRelatedIdsByType(serviceNode.id, "pod", ["depends_on", "contains", "runs_on"]).length > 0;
    const isBridgeService = Boolean(hasComputeDependency && hasDownstreamPods);
    positions.set(item.id, { x: isBridgeService ? Math.min(position.x, serviceBridgeX) : position.x, y: position.y });
    serviceYById.set(item.id, position.y);
  });

  const podsByService = new Map<string, TopologyObject[]>();

  pods.forEach((pod) => {
    const ownerService = getRelatedIdsByType(pod.id, "service", ["depends_on", "contains", "runs_on"])[0];
    const list = podsByService.get(ownerService ?? "unassigned") ?? [];
    list.push(pod);
    podsByService.set(ownerService ?? "unassigned", list);
  });

  const podAnchorGap = Math.round(metrics.nodeHeight * 0.56);
  const podLaneGap = Math.round(metrics.nodeHeight * 0.74);
  let unassignedPodAnchorY = Math.max(
    getMaxPlacedY() + Math.round(metrics.nodeHeight * 0.84),
    Math.round(switchCenterY + metrics.nodeHeight * 2.85),
  );

  const serviceOrder = [...serviceYById.entries()]
    .sort((left, right) => left[1] - right[1])
    .map(([id]) => id);

  const podAnchors: AnchoredLaneItem[] = [];

  serviceOrder.forEach((serviceId) => {
    const anchorY = serviceYById.get(serviceId);
    const servicePods = [...(podsByService.get(serviceId) ?? [])].sort((left, right) =>
      compareNodes(left, right, incomingEdges, outgoingEdges),
    );

    if (anchorY === undefined) {
      servicePods.forEach((pod) => {
        podAnchors.push({
          id: pod.id,
          anchorY: unassignedPodAnchorY,
          tieBreaker: "pod-unassigned-" + pod.name + "-" + pod.id,
        });
        unassignedPodAnchorY += podLaneGap;
      });
      return;
    }

    servicePods.forEach((pod, index) => {
      podAnchors.push({
        id: pod.id,
        anchorY: anchorY + getCenteredOffset(index, servicePods.length, podAnchorGap),
        tieBreaker: "pod-" + anchorY + "-" + index + "-" + pod.name + "-" + pod.id,
      });
    });
  });

  [...(podsByService.get("unassigned") ?? [])]
    .sort((left, right) => compareNodes(left, right, incomingEdges, outgoingEdges))
    .forEach((pod, index) => {
      podAnchors.push({
        id: pod.id,
        anchorY: unassignedPodAnchorY,
        tieBreaker: "pod-unassigned-tail-" + index + "-" + pod.name + "-" + pod.id,
      });
      unassignedPodAnchorY += podLaneGap;
    });

  const podLaneYById = placeLaneByAnchors(podAnchors, podLaneGap);
  const podColumnGap = Math.round(metrics.nodeWidth * 1.1);
  const podFlowStartY = Math.round(metrics.layerYOffset + metrics.nodeHeight * 0.48);
  const podFlowPositions = placeExpandedLane(podAnchors, {
    startX: podX,
    startY: podFlowStartY,
    rowGap: podLaneGap,
    columnGap: podColumnGap,
    maxRows: 10,
  });
  podAnchors.forEach((item) => {
    const fallbackY = podLaneYById.get(item.id) ?? Math.round(item.anchorY);
    const position = podFlowPositions.get(item.id) ?? { x: podX, y: fallbackY };
    positions.set(item.id, position);
  });
  const resolveHostForBranchNode = (entity: TopologyObject) => {
    const connectedHost = getRelatedIdsByType(entity.id, "node", ["contains", "runs_on", "connects_to", "depends_on"])[0];
    if (connectedHost) {
      return connectedHost;
    }

    const hostFromAttr = typeof entity.attributes.host === "string" ? entity.attributes.host : undefined;
    if (hostFromAttr && nodeById.get(hostFromAttr)?.type === "node") {
      return hostFromAttr;
    }

    const aggregateHostId = typeof entity.attributes.aggregateHostId === "string" ? entity.attributes.aggregateHostId : undefined;
    if (aggregateHostId && nodeById.get(aggregateHostId)?.type === "node") {
      return aggregateHostId;
    }

    return undefined;
  };

  const bmcByHost = new Map<string, TopologyObject[]>();
  bmcs.forEach((bmc) => {
    const hostId = resolveHostForBranchNode(bmc) ?? "unassigned";
    const list = bmcByHost.get(hostId) ?? [];
    list.push(bmc);
    bmcByHost.set(hostId, list);
  });

  const gpuByHost = new Map<string, TopologyObject[]>();
  gpus.forEach((gpu) => {
    const hostId = resolveHostForBranchNode(gpu) ?? "unassigned";
    const list = gpuByHost.get(hostId) ?? [];
    list.push(gpu);
    gpuByHost.set(hostId, list);
  });

  const branchBandOffset = Math.round(metrics.nodeHeight * 0.72);
  const branchStackGap = Math.round(metrics.nodeHeight * 0.58);
  let unassignedBranchY = Math.max(
    getMaxPlacedY() + Math.round(metrics.nodeHeight * 0.8),
    Math.round(switchCenterY + metrics.nodeHeight * 2.4),
  );

  const branchAnchors: AnchoredLaneItem[] = [];

  const pushAnchoredGroup = (hostId: string, items: TopologyObject[], direction: "up" | "down") => {
    const hostY = workerYById.get(hostId);
    if (hostId === "unassigned" || hostY === undefined) {
      items.forEach((item, index) => {
        branchAnchors.push({
          id: item.id,
          anchorY: unassignedBranchY + index * branchStackGap,
          tieBreaker: `branch-unassigned-${direction}-${index}-${item.name}-${item.id}`,
        });
      });
      unassignedBranchY += Math.max(1, items.length) * branchStackGap;
      return;
    }

    const center = direction === "up" ? hostY - branchBandOffset : hostY + branchBandOffset;
    items.forEach((item, index) => {
      branchAnchors.push({
        id: item.id,
        anchorY: center + getCenteredOffset(index, items.length, branchStackGap),
        tieBreaker: `branch-${hostY}-${direction}-${index}-${item.name}-${item.id}`,
      });
    });
  };

  const orderedHostIds = (grouped: Map<string, TopologyObject[]>) =>
    [...grouped.keys()].sort((left, right) => {
      if (left === "unassigned" || right === "unassigned") {
        return left === "unassigned" ? 1 : -1;
      }

      const leftY = workerYById.get(left) ?? Number.MAX_SAFE_INTEGER;
      const rightY = workerYById.get(right) ?? Number.MAX_SAFE_INTEGER;
      if (leftY !== rightY) {
        return leftY - rightY;
      }

      return left.localeCompare(right, "zh-Hans-CN");
    });

  orderedHostIds(bmcByHost).forEach((hostId) => {
    const items = [...(bmcByHost.get(hostId) ?? [])].sort((left, right) =>
      compareNodes(left, right, incomingEdges, outgoingEdges),
    );
    pushAnchoredGroup(hostId, items, "up");
  });

  orderedHostIds(gpuByHost).forEach((hostId) => {
    const items = [...(gpuByHost.get(hostId) ?? [])].sort((left, right) =>
      compareNodes(left, right, incomingEdges, outgoingEdges),
    );
    pushAnchoredGroup(hostId, items, "down");
  });

  const branchLaneYById = placeLaneByAnchors(branchAnchors, branchStackGap);
  branchAnchors.forEach((item) => {
    const y = branchLaneYById.get(item.id) ?? Math.round(item.anchorY);
    positions.set(item.id, { x: branchX, y: Math.round(y) });
  });

  return positions;
}
function getLayeredCollisionRadius(node: TopologyObject, metrics: TopologyCanvasMetrics) {
  const aggregateCount = getAggregateCount(node);
  const baseRadius = metrics.nodeCircleSize / 2 + 3;

  if (aggregateCount > 0) {
    return baseRadius + Math.min(14, aggregateCount) * 0.35 + 4;
  }

  if (node.type === "cluster") {
    return baseRadius + 4;
  }

  if (node.type === "service" || node.type === "pod") {
    return baseRadius + 1;
  }

  return baseRadius;
}

function resolveLayeredVerticalOverlaps(
  baseTargets: Map<string, { x: number; y: number }>,
  nodes: TopologyObject[],
  metrics: TopologyCanvasMetrics,
) {
  const result = new Map<string, { x: number; y: number }>();
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const lanes = new Map<number, Array<{ id: string; x: number; anchorY: number; y: number; radius: number }>>();

  baseTargets.forEach((target, nodeId) => {
    const node = nodeById.get(nodeId);
    if (!node) {
      return;
    }

    const laneKey = Math.round(target.x / 4) * 4;
    const radius = getLayeredCollisionRadius(node, metrics);
    const lane = lanes.get(laneKey) ?? [];
    lane.push({
      id: nodeId,
      x: target.x,
      anchorY: target.y,
      y: target.y,
      radius,
    });
    lanes.set(laneKey, lane);
  });

  lanes.forEach((lane) => {
    lane.sort((left, right) => left.anchorY - right.anchorY);

    for (let index = 1; index < lane.length; index += 1) {
      const previous = lane[index - 1];
      const current = lane[index];
      const minY = previous.y + previous.radius + current.radius + 6;
      if (current.y < minY) {
        current.y = minY;
      }
    }

    for (let index = lane.length - 2; index >= 0; index -= 1) {
      const current = lane[index];
      const next = lane[index + 1];
      const maxY = next.y - (current.radius + next.radius + 6);
      if (current.y > maxY) {
        current.y = maxY;
      }
    }

    const anchorCenter = lane.reduce((sum, item) => sum + item.anchorY, 0) / Math.max(1, lane.length);
    const currentCenter = lane.reduce((sum, item) => sum + item.y, 0) / Math.max(1, lane.length);
    const shift = anchorCenter - currentCenter;

    lane.forEach((item) => {
      item.y += shift;
    });

    for (let index = 1; index < lane.length; index += 1) {
      const previous = lane[index - 1];
      const current = lane[index];
      const minY = previous.y + previous.radius + current.radius + 6;
      if (current.y < minY) {
        current.y = minY;
      }
    }

    lane.forEach((item) => {
      result.set(item.id, {
        x: Math.round(item.x),
        y: Math.round(item.y),
      });
    });
  });

  return result;
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
    const relaxedTargets = resolveLayeredVerticalOverlaps(baseTargets, nodes, metrics);

    let lowestY = Number.POSITIVE_INFINITY;
    relaxedTargets.forEach((target) => {
      lowestY = Math.min(lowestY, target.y);
    });
    const yShift = Number.isFinite(lowestY) && lowestY < minY ? minY - lowestY : 0;

    relaxedTargets.forEach((target, nodeId) => {
      positions.set(nodeId, {
        x: Math.max(minX, Math.round(target.x)),
        y: Math.round(target.y + yShift),
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

function placeObjectLane(
  positions: Map<string, { x: number; y: number }>,
  items: TopologyObject[],
  x: number,
  centerY: number,
  gap: number,
) {
  const ordered = [...items].sort((left, right) => left.name.localeCompare(right.name, "zh-Hans-CN"));
  ordered.forEach((node, index) => {
    positions.set(node.id, {
      x,
      y: Math.round(centerY + getCenteredOffset(index, ordered.length, gap)),
    });
  });
}

export function computeObjectFocusLayout(
  nodes: TopologyObject[],
  edges: TopologyRelation[],
  focalNodeId: string | undefined,
  metrics: TopologyCanvasMetrics,
) {
  const positions = new Map<string, { x: number; y: number }>();
  if (nodes.length === 0) {
    return positions;
  }

  const fallbackFocalId = focalNodeId && nodes.some((node) => node.id === focalNodeId) ? focalNodeId : nodes[0]?.id;
  if (!fallbackFocalId) {
    return positions;
  }

  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const focal = nodeById.get(fallbackFocalId);
  if (!focal) {
    return positions;
  }

  const lanes = {
    upstream: [] as TopologyObject[],
    downstream: [] as TopologyObject[],
    upper: [] as TopologyObject[],
    lower: [] as TopologyObject[],
    detached: [] as TopologyObject[],
  };
  const assigned = new Set<string>([focal.id]);
  const pushLane = (lane: keyof typeof lanes, nodeId: string) => {
    const node = nodeById.get(nodeId);
    if (!node || assigned.has(node.id)) {
      return;
    }
    lanes[lane].push(node);
    assigned.add(node.id);
  };

  edges.forEach((edge) => {
    if (edge.source !== focal.id && edge.target !== focal.id) {
      return;
    }

    if (edge.relationType === "contains") {
      pushLane(edge.target === focal.id ? "upper" : "lower", edge.target === focal.id ? edge.source : edge.target);
      return;
    }

    if (edge.target === focal.id) {
      pushLane("upstream", edge.source);
      return;
    }

    pushLane("downstream", edge.target);
  });

  nodes.forEach((node) => {
    if (!assigned.has(node.id)) {
      lanes.detached.push(node);
    }
  });

  const centerX = Math.round(metrics.nodeWidth * 4.1);
  const centerY = Math.round(metrics.nodeHeight * 2.35);
  const horizontalGap = Math.round(metrics.nodeWidth * 2.7);
  const verticalGap = Math.round(metrics.nodeHeight * 1.55);
  const laneGap = Math.round(metrics.nodeHeight * 1.3);

  positions.set(focal.id, { x: centerX, y: centerY });
  placeObjectLane(positions, lanes.upstream, centerX - horizontalGap, centerY, laneGap);
  placeObjectLane(positions, lanes.downstream, centerX + horizontalGap, centerY, laneGap);
  placeObjectLane(positions, lanes.upper, centerX, centerY - verticalGap, Math.round(laneGap * 0.9));
  placeObjectLane(positions, lanes.lower, centerX, centerY + verticalGap, Math.round(laneGap * 0.9));
  placeObjectLane(positions, lanes.detached, centerX + horizontalGap, centerY + verticalGap, laneGap);

  return positions;
}
