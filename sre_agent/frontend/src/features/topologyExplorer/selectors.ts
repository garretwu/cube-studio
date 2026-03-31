import type {
  TopologyExplorerResponse,
  TopologyObject,
  TopologyObjectStatus,
  TopologyPath,
  TopologyRelation,
} from "../../api/types";
import type { ExplorerLayerFilter, ExplorerSummaryFilter, ExplorerStatusFilter, TopologyExplorerFilters } from "./types";

export type TopologySummaryMetrics = {
  totalEntities: number;
  abnormalEntities: number;
  impactedEntities: number;
  activePaths: number;
};

export type TopologyTreeNode = {
  id: string;
  label: string;
  type: "group" | "object";
  objectId?: string;
  objectType?: TopologyObject["type"];
  children?: TopologyTreeNode[];
};

export type ImpactTopology = {
  path: TopologyPath;
  orderedPaths: TopologyPath[];
  nodes: TopologyObject[];
  edges: TopologyRelation[];
  rootCauseNode?: TopologyObject;
  entryNode?: TopologyObject;
  affectedNodes: TopologyObject[];
  blastRadiusCount: number;
};

function toLookupMap<T extends { id: string }>(items: T[]) {
  return new Map(items.map((item) => [item.id, item]));
}

function unique<T>(items: T[]) {
  return Array.from(new Set(items));
}

function statusRank(status: TopologyObjectStatus) {
  const rank: Record<TopologyObjectStatus, number> = {
    healthy: 0,
    maintenance: 1,
    impacted: 2,
    abnormal: 3,
  };

  return rank[status];
}

function impactRank(level: TopologyRelation["impactLevel"]) {
  const rank: Record<TopologyRelation["impactLevel"], number> = {
    low: 0,
    medium: 1,
    high: 2,
  };

  return rank[level];
}

function pathStatusRank(status: TopologyPath["status"]) {
  return status === "active" ? 1 : 0;
}

function sortImpactPaths(paths: TopologyPath[]) {
  return [...paths].sort((left, right) => {
    const statusDelta = pathStatusRank(right.status) - pathStatusRank(left.status);
    if (statusDelta !== 0) {
      return statusDelta;
    }

    const impactDelta = impactRank(right.impactLevel) - impactRank(left.impactLevel);
    if (impactDelta !== 0) {
      return impactDelta;
    }

    const affectedDelta = right.affectedNodeIds.length - left.affectedNodeIds.length;
    if (affectedDelta !== 0) {
      return affectedDelta;
    }

    const edgeDelta = right.edgeIds.length - left.edgeIds.length;
    if (edgeDelta !== 0) {
      return edgeDelta;
    }

    return left.id.localeCompare(right.id);
  });
}

function normalizeSearch(query: string) {
  return query.trim().toLowerCase();
}

function getOutgoing(edges: TopologyRelation[]) {
  const outgoing = new Map<string, TopologyRelation[]>();

  edges.forEach((edge) => {
    const list = outgoing.get(edge.source) ?? [];
    list.push(edge);
    outgoing.set(edge.source, list);
  });

  return outgoing;
}

function getUndirected(edges: TopologyRelation[]) {
  const neighbors = new Map<string, string[]>();

  edges.forEach((edge) => {
    const sourceList = neighbors.get(edge.source) ?? [];
    sourceList.push(edge.target);
    neighbors.set(edge.source, sourceList);

    const targetList = neighbors.get(edge.target) ?? [];
    targetList.push(edge.source);
    neighbors.set(edge.target, targetList);
  });

  return neighbors;
}

function getEdgeMap(edges: TopologyRelation[]) {
  return new Map(edges.map((edge) => [edge.id, edge]));
}

function intersectSets(base: Set<string>, next: Set<string>) {
  return new Set(Array.from(base).filter((value) => next.has(value)));
}

function collectPathContext(
  response: TopologyExplorerResponse,
  pathPredicate: (path: TopologyPath) => boolean,
) {
  const matchingPaths = response.paths.filter(pathPredicate);
  const edgeMap = getEdgeMap(response.edges);
  const nodeIds = new Set<string>();

  matchingPaths.forEach((path) => {
    nodeIds.add(path.entryNodeId);
    nodeIds.add(path.rootCauseNodeId);
    path.affectedNodeIds.forEach((id) => nodeIds.add(id));
    path.edgeIds.forEach((edgeId) => {
      const edge = edgeMap.get(edgeId);
      if (edge) {
        nodeIds.add(edge.source);
        nodeIds.add(edge.target);
      }
    });
  });

  return nodeIds;
}

export function searchTopologyObjects(nodes: TopologyObject[], query: string) {
  const normalized = normalizeSearch(query);
  if (!normalized) {
    return [];
  }

  return nodes.filter((node) => {
    const haystack = [
      node.id,
      node.name,
      node.type,
      node.status,
      node.layer,
      node.domain,
      node.region,
      node.zone,
      node.cluster,
      node.rack,
      node.summary,
      ...node.tags,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    return haystack.includes(normalized);
  });
}

export function getSummaryMetrics(response?: TopologyExplorerResponse): TopologySummaryMetrics {
  if (!response) {
    return {
      totalEntities: 0,
      abnormalEntities: 0,
      impactedEntities: 0,
      activePaths: 0,
    };
  }

  return {
    totalEntities: response.nodes.length,
    abnormalEntities: response.nodes.filter((node) => node.status === "abnormal").length,
    impactedEntities: response.nodes.filter((node) => node.status === "impacted").length,
    activePaths: response.paths.filter((path) => path.status === "active").length,
  };
}

export function getOrderedImpactPaths(paths: TopologyPath[]) {
  return sortImpactPaths(paths.filter((path) => path.status === "active"));
}

export function getPrimaryImpactPathId(response?: TopologyExplorerResponse) {
  if (!response) {
    return undefined;
  }

  return getOrderedImpactPaths(response.paths)[0]?.id;
}

export function getImpactPathIdForNode(paths: TopologyPath[], nodeId?: string) {
  if (!nodeId) {
    return undefined;
  }

  return getOrderedImpactPaths(paths).find(
    (path) =>
      path.rootCauseNodeId === nodeId ||
      path.entryNodeId === nodeId ||
      path.affectedNodeIds.includes(nodeId),
  )?.id;
}

export function getNeighborDepths(edges: TopologyRelation[], nodeId?: string, maxDepth = 2) {
  if (!nodeId) {
    return new Map<string, number>();
  }

  const undirected = getUndirected(edges);
  const visited = new Map<string, number>([[nodeId, 0]]);
  const queue: Array<{ id: string; depth: number }> = [{ id: nodeId, depth: 0 }];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || current.depth >= maxDepth) {
      continue;
    }

    const neighbors = undirected.get(current.id) ?? [];
    neighbors.forEach((neighborId) => {
      const nextDepth = current.depth + 1;
      const previousDepth = visited.get(neighborId);
      if (previousDepth !== undefined && previousDepth <= nextDepth) {
        return;
      }

      visited.set(neighborId, nextDepth);
      queue.push({ id: neighborId, depth: nextDepth });
    });
  }

  return visited;
}

function getFilterContextIds(
  response: TopologyExplorerResponse,
  statusFilter: ExplorerStatusFilter,
  summaryFilter: ExplorerSummaryFilter,
) {
  let contextIds = new Set(response.nodes.map((node) => node.id));
  const abnormalIds = new Set(response.nodes.filter((node) => node.status === "abnormal").map((node) => node.id));
  const impactedIds = new Set(response.nodes.filter((node) => node.status === "impacted").map((node) => node.id));
  const activePathIds = collectPathContext(response, (path) => path.status === "active");
  const abnormalContext = collectPathContext(
    response,
    (path) =>
      path.status === "active" &&
      (abnormalIds.has(path.rootCauseNodeId) ||
        abnormalIds.has(path.entryNodeId) ||
        path.affectedNodeIds.some((id) => abnormalIds.has(id))),
  );
  const impactedContext = collectPathContext(
    response,
    (path) =>
      path.status === "active" &&
      (impactedIds.has(path.entryNodeId) || path.affectedNodeIds.some((id) => impactedIds.has(id))),
  );

  abnormalIds.forEach((id) => abnormalContext.add(id));
  impactedIds.forEach((id) => impactedContext.add(id));

  if (summaryFilter === "abnormal") {
    contextIds = intersectSets(contextIds, abnormalContext);
  } else if (summaryFilter === "impacted") {
    contextIds = intersectSets(contextIds, impactedContext);
  } else if (summaryFilter === "paths") {
    contextIds = intersectSets(contextIds, activePathIds);
  }

  if (statusFilter === "abnormal") {
    contextIds = intersectSets(contextIds, abnormalContext);
  }

  return contextIds;
}

function filterByLayer(nodes: TopologyObject[], layerFilter: ExplorerLayerFilter) {
  if (layerFilter === "all") {
    return nodes;
  }

  return nodes.filter((node) => node.layer === layerFilter);
}

function buildAggregatedEdges(
  response: TopologyExplorerResponse,
  visibleNodeIds: Set<string>,
  contextIds: Set<string>,
) {
  const outgoing = getOutgoing(response.edges.filter((edge) => contextIds.has(edge.source) && contextIds.has(edge.target)));
  const nodeMap = toLookupMap(response.nodes);
  const directKeys = new Set(
    response.edges
      .filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
      .map((edge) => `${edge.source}:${edge.target}`),
  );
  const aggregated = new Map<string, TopologyRelation>();

  visibleNodeIds.forEach((sourceId) => {
    const initialEdges = (outgoing.get(sourceId) ?? []).filter((edge) => !visibleNodeIds.has(edge.target));
    const queue = initialEdges.map((edge) => ({
      currentId: edge.target,
      traversedEdges: [edge],
      visitedHiddenIds: new Set([edge.target]),
    }));

    while (queue.length > 0) {
      const current = queue.shift();
      if (!current) {
        continue;
      }

      const nextEdges = outgoing.get(current.currentId) ?? [];
      nextEdges.forEach((nextEdge) => {
        if (!contextIds.has(nextEdge.target) || nextEdge.target === sourceId) {
          return;
        }

        const traversedEdges = [...current.traversedEdges, nextEdge];
        if (visibleNodeIds.has(nextEdge.target)) {
          const aggregateKey = `${sourceId}:${nextEdge.target}`;
          if (directKeys.has(aggregateKey)) {
            return;
          }

          const pathStatus = traversedEdges.reduce<TopologyObjectStatus>((winner, edge) => {
            return statusRank(edge.status) > statusRank(winner) ? edge.status : winner;
          }, "healthy");
          const withNodeStatus = [sourceId, nextEdge.target]
            .map((id) => nodeMap.get(id)?.status)
            .filter((status): status is TopologyObjectStatus => Boolean(status))
            .reduce<TopologyObjectStatus>(
              (winner, status) => (statusRank(status) > statusRank(winner) ? status : winner),
              pathStatus,
            );
          const impactLevel = traversedEdges.reduce<TopologyRelation["impactLevel"]>((winner, edge) => {
            return impactRank(edge.impactLevel) > impactRank(winner) ? edge.impactLevel : winner;
          }, "low");

          aggregated.set(aggregateKey, {
            id: `aggregated-${sourceId}-${nextEdge.target}`,
            source: sourceId,
            target: nextEdge.target,
            relationType: "aggregated",
            status: withNodeStatus,
            isCritical: traversedEdges.some((edge) => edge.isCritical),
            impactLevel,
            label: "跨层聚合",
            isAggregated: true,
          });
          return;
        }

        if (current.visitedHiddenIds.has(nextEdge.target)) {
          return;
        }

        queue.push({
          currentId: nextEdge.target,
          traversedEdges,
          visitedHiddenIds: new Set(current.visitedHiddenIds).add(nextEdge.target),
        });
      });
    }
  });

  return Array.from(aggregated.values());
}

export function getVisibleTopology(
  response: TopologyExplorerResponse | undefined,
  filters: TopologyExplorerFilters,
) {
  if (!response) {
    return {
      nodes: [] as TopologyObject[],
      edges: [] as TopologyRelation[],
      contextIds: new Set<string>(),
      visibleNodeIds: new Set<string>(),
    };
  }

  const contextIds = getFilterContextIds(response, filters.statusFilter, filters.summaryFilter);
  const visibleNodes = filterByLayer(
    response.nodes.filter((node) => contextIds.has(node.id)),
    filters.layerFilter,
  );
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const directEdges = response.edges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target));
  const aggregatedEdges =
    filters.layerFilter === "all" ? [] : buildAggregatedEdges(response, visibleNodeIds, contextIds);

  return {
    nodes: visibleNodes,
    edges: [...directEdges, ...aggregatedEdges],
    contextIds,
    visibleNodeIds,
  };
}

export function getPathsForNode(paths: TopologyPath[], nodeId?: string) {
  if (!nodeId) {
    return [];
  }

  return paths.filter(
    (path) =>
      path.entryNodeId === nodeId ||
      path.rootCauseNodeId === nodeId ||
      path.affectedNodeIds.includes(nodeId),
  );
}

export function getImpactTopology(response: TopologyExplorerResponse | undefined, pathId?: string): ImpactTopology | null {
  if (!response || !pathId) {
    return null;
  }

  const orderedPaths = getOrderedImpactPaths(response.paths);
  const activePath = orderedPaths.find((path) => path.id === pathId);
  if (!activePath) {
    return null;
  }

  const edgeMap = getEdgeMap(response.edges);
  const nodeMap = toLookupMap(response.nodes);
  const nodeIds = collectPathContext(response, (path) => path.id === activePath.id);
  const edges = unique(activePath.edgeIds)
    .map((edgeId) => edgeMap.get(edgeId))
    .filter((edge): edge is TopologyRelation => Boolean(edge));
  const affectedNodes = activePath.affectedNodeIds
    .map((nodeId) => nodeMap.get(nodeId))
    .filter((node): node is TopologyObject => Boolean(node));

  return {
    path: activePath,
    orderedPaths,
    nodes: response.nodes.filter((node) => nodeIds.has(node.id)),
    edges,
    rootCauseNode: nodeMap.get(activePath.rootCauseNodeId),
    entryNode: nodeMap.get(activePath.entryNodeId),
    affectedNodes,
    blastRadiusCount: activePath.affectedNodeIds.length,
  };
}

export function getRelationsForNode(response: TopologyExplorerResponse | undefined, nodeId?: string) {
  if (!response || !nodeId) {
    return { upstream: [] as TopologyObject[], downstream: [] as TopologyObject[], neighbors: [] as TopologyObject[] };
  }

  const nodeMap = toLookupMap(response.nodes);
  const upstream = response.edges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => nodeMap.get(edge.source))
    .filter((node): node is TopologyObject => Boolean(node));
  const downstream = response.edges
    .filter((edge) => edge.source === nodeId)
    .map((edge) => nodeMap.get(edge.target))
    .filter((node): node is TopologyObject => Boolean(node));
  const neighbors = unique([...upstream, ...downstream]);

  return { upstream, downstream, neighbors };
}

export function getAffectedObjectsForNode(response: TopologyExplorerResponse | undefined, nodeId?: string) {
  if (!response || !nodeId) {
    return [];
  }

  const nodeMap = toLookupMap(response.nodes);
  return unique(
    getPathsForNode(response.paths, nodeId)
      .flatMap((path) => path.affectedNodeIds)
      .map((id) => nodeMap.get(id))
      .filter((node): node is TopologyObject => Boolean(node)),
  );
}

export function buildTopologyTree(response: TopologyExplorerResponse | undefined): TopologyTreeNode | null {
  if (!response) {
    return null;
  }

  const racks = response.nodes.filter((node) => node.type === "rack");
  const nodeMap = toLookupMap(response.nodes);
  const containsEdges = response.edges.filter((edge) => edge.relationType === "contains");
  const serviceEdges = response.edges.filter((edge) => edge.relationType === "runs_on");

  const rackNodes = racks.map<TopologyTreeNode>((rack) => {
    const computeNodes = containsEdges
      .filter((edge) => edge.source === rack.id)
      .map((edge) => nodeMap.get(edge.target))
      .filter((node): node is TopologyObject => Boolean(node))
      .map<TopologyTreeNode>((node) => {
        const gpus = containsEdges
          .filter((edge) => edge.source === node.id)
          .map((edge) => nodeMap.get(edge.target))
          .filter((gpu): gpu is TopologyObject => Boolean(gpu))
          .map<TopologyTreeNode>((gpu) => ({
            id: gpu.id,
            label: gpu.name,
            type: "object",
            objectId: gpu.id,
            objectType: gpu.type,
          }));
        const services = serviceEdges
          .filter((edge) => edge.target === node.id)
          .map((edge) => nodeMap.get(edge.source))
          .filter((service): service is TopologyObject => Boolean(service))
          .map<TopologyTreeNode>((service) => ({
            id: service.id,
            label: service.name,
            type: "object",
            objectId: service.id,
            objectType: service.type,
          }));

        return {
          id: node.id,
          label: node.name,
          type: "object",
          objectId: node.id,
          objectType: node.type,
          children: [...gpus, ...services],
        };
      });

    return {
      id: rack.id,
      label: rack.name,
      type: "object",
      objectId: rack.id,
      objectType: rack.type,
      children: computeNodes,
    };
  });

  const clusterNodes = response.nodes
    .filter((node) => node.type === "cluster")
    .map<TopologyTreeNode>((cluster) => ({
      id: cluster.id,
      label: cluster.name,
      type: "object",
      objectId: cluster.id,
      objectType: cluster.type,
      children: response.nodes
        .filter((node) => node.type === "service" && node.cluster === cluster.id)
        .map((service) => ({
          id: service.id,
          label: service.name,
          type: "object" as const,
          objectId: service.id,
          objectType: service.type,
        })),
    }));

  const switchNodes = response.nodes
    .filter((node) => node.type === "switch")
    .map<TopologyTreeNode>((switchNode) => ({
      id: switchNode.id,
      label: switchNode.name,
      type: "object",
      objectId: switchNode.id,
      objectType: switchNode.type,
    }));

  return {
    id: response.site.id,
    label: response.site.name,
    type: "group",
    children: [
      {
        id: `${response.site.id}-facility`,
        label: `${response.site.region} / ${response.site.zone}`,
        type: "group",
        children: rackNodes,
      },
      {
        id: `${response.site.id}-cluster`,
        label: "集群与服务",
        type: "group",
        children: clusterNodes,
      },
      {
        id: `${response.site.id}-network`,
        label: "网络设施",
        type: "group",
        children: switchNodes,
      },
    ],
  };
}
