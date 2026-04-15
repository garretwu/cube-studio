import type {
  TopologyExplorerResponse,
  TopologyObject,
  TopologyObjectStatus,
  TopologyPath,
  TopologyRelation,
} from "../../api/types";
import type {
  ExplorerLayerFilter,
  ExplorerSummaryFilter,
  ExplorerStatusFilter,
  TopologyExplorerFilters,
} from "./types";

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

export const GLOBAL_TOPOLOGY_SERVICE_NODE_LIMIT = 20;
export const MODIFIED_SERVICE_AGGREGATE_MIN_MEMBERS = 4;
export const SYNTHETIC_SERVICE_AGGREGATE_KIND = "serviceAggregate";
export const SYNTHETIC_GPU_AGGREGATE_KIND = "gpuAggregate";

export function isSyntheticTopologyNode(node: TopologyObject) {
  return typeof node.attributes.syntheticKind === "string";
}

export function isSyntheticServiceAggregateNode(node: TopologyObject) {
  return node.attributes.syntheticKind === SYNTHETIC_SERVICE_AGGREGATE_KIND;
}

export function isSyntheticGpuAggregateNode(node: TopologyObject) {
  return node.attributes.syntheticKind === SYNTHETIC_GPU_AGGREGATE_KIND;
}

export function getSyntheticAggregateGroupId(node: TopologyObject) {
  const value = node.attributes.aggregateGroupId;
  return typeof value === "string" ? value : undefined;
}
function isGlobalTopologyCappedNode(node: TopologyObject) {
  return node.layer === "service";
}

export function getGlobalTopologyDisplayData(
  response: TopologyExplorerResponse | undefined,
) {
  if (!response) {
    return undefined;
  }

  const visibleServiceNodeIds = new Set(
    response.nodes
      .filter((node) => isGlobalTopologyCappedNode(node))
      .slice(0, GLOBAL_TOPOLOGY_SERVICE_NODE_LIMIT)
      .map((node) => node.id),
  );
  const nodes = response.nodes.filter(
    (node) =>
      !isGlobalTopologyCappedNode(node) || visibleServiceNodeIds.has(node.id),
  );
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const edges = response.edges.filter(
    (edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
  );

  return {
    ...response,
    nodes,
    edges,
  };
}

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

function getRawType(node: TopologyObject) {
  return String(node.attributes.rawType ?? node.type);
}

function getHighestStatus(nodes: TopologyObject[]) {
  return nodes.reduce<TopologyObjectStatus>((winner, node) => {
    return statusRank(node.status) > statusRank(winner) ? node.status : winner;
  }, "healthy");
}
function getRelationLabel(edge: Pick<TopologyRelation, "relationType" | "label">) {
  const rawLabel = typeof edge.label === "string" ? edge.label.trim() : "";
  if (rawLabel.length > 0) {
    return rawLabel;
  }
  return edge.relationType.replace(/_/g, " ");
}

function buildAggregatedRelationLabel(relationLabels: Set<string>, count: number) {
  if (relationLabels.size === 1) {
    const [relationLabel] = Array.from(relationLabels);
    return `${relationLabel}(${count})`;
  }
  return `${count} relations`;
}
function pathStatusRank(status: TopologyPath["status"]) {
  return status === "active" ? 1 : 0;
}

function sortImpactPaths(paths: TopologyPath[]) {
  return [...paths].sort((left, right) => {
    const statusDelta =
      pathStatusRank(right.status) - pathStatusRank(left.status);
    if (statusDelta !== 0) {
      return statusDelta;
    }

    const impactDelta =
      impactRank(right.impactLevel) - impactRank(left.impactLevel);
    if (impactDelta !== 0) {
      return impactDelta;
    }

    const affectedDelta =
      right.affectedNodeIds.length - left.affectedNodeIds.length;
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

function getAggregateHostId(
  node: TopologyObject,
  outgoingEdges: Map<string, TopologyRelation[]>,
) {
  const hostEdge = (outgoingEdges.get(node.id) ?? []).find(
    (edge) => edge.relationType === "runs_on",
  );
  return hostEdge?.target ?? node.cluster ?? node.rack ?? node.domain;
}

type ModifiedAggregationOptions = {
  expandedAggregateIds?: string[];
  priorityNodeIds?: string[];
};

function buildModifiedAggregatedTopology(
  response: TopologyExplorerResponse,
  options: ModifiedAggregationOptions = {},
) {
  const nodeMap = toLookupMap(response.nodes);
  const outgoing = getOutgoing(response.edges);
  const incoming = new Map<string, TopologyRelation[]>();
  response.edges.forEach((edge) => {
    const list = incoming.get(edge.target) ?? [];
    list.push(edge);
    incoming.set(edge.target, list);
  });
  const expandedAggregateIds = new Set(options.expandedAggregateIds ?? []);
  const priorityNodeIds = new Set(options.priorityNodeIds ?? []);
  const groupedMembers = new Map<string, TopologyObject[]>();

  response.nodes.forEach((node) => {
    if (node.layer !== "service") {
      return;
    }

    // Keep namespace-level service objects visible; only aggregate Pod objects under their owning service.
    if (node.type !== "pod" && String(node.attributes.rawType ?? "").trim().toLowerCase() !== "pod") {
      return;
    }
    const ownerService = (incoming.get(node.id) ?? [])
      .map((edge) => nodeMap.get(edge.source))
      .find((candidate) => Boolean(candidate && candidate.type === "service"));
    const serviceKey = ownerService?.id ?? "unassigned";
    const aggregateGroupId = `aggregate:${serviceKey}:pod`;
    const list = groupedMembers.get(aggregateGroupId) ?? [];
    list.push(node);
    groupedMembers.set(aggregateGroupId, list);
  });

  response.nodes.forEach((node) => {
    if (node.type !== "gpu") {
      return;
    }
    const hostNodeId = getGpuHostNodeId(node, nodeMap, outgoing, incoming) ?? "unassigned";
    const aggregateGroupId = `aggregate-gpu:${hostNodeId}`;
    const list = groupedMembers.get(aggregateGroupId) ?? [];
    list.push(node);
    groupedMembers.set(aggregateGroupId, list);
  });

  const replacementMap = new Map<string, string>();
  const aggregateNodes: TopologyObject[] = [];

  groupedMembers.forEach((members, aggregateGroupId) => {
    if (aggregateGroupId.startsWith("aggregate-gpu:")) {
      const hostNodeId = aggregateGroupId.slice("aggregate-gpu:".length) || "unassigned";
      const fixedMembers = members.filter(
        (node) => node.status !== "healthy" || priorityNodeIds.has(node.id),
      );
      const hiddenMembers = members.filter((node) => !fixedMembers.includes(node));

      // Always show a dedicated GPU group node when there are multiple GPUs on the host.
      // Healthy GPUs are collapsed into the group by default; abnormal/search-hit GPUs stay visible.
      if (members.length < 2 || expandedAggregateIds.has(aggregateGroupId)) {
        return;
      }

      hiddenMembers.forEach((node) => {
        replacementMap.set(node.id, aggregateGroupId);
      });

      const hostNode = nodeMap.get(hostNodeId);
      const hostLabel = hostNode?.name ?? hostNodeId;
      const aggregateStatus = getHighestStatus(members);
      const latestUpdatedAt = members
        .map((node) => node.updatedAt)
        .sort((left, right) => right.localeCompare(left))[0] ?? response.lastUpdated;
      const sharedTags = unique(members.flatMap((node) => node.tags));
      const cluster = members.find((node) => Boolean(node.cluster))?.cluster;
      const rack = members.find((node) => Boolean(node.rack))?.rack;

      aggregateNodes.push({
        id: aggregateGroupId,
        name: `GPUç»?\u00b7 ${members.length} \u00b7 ${hostLabel}`,
        type: "gpu",
        status: aggregateStatus,
        layer: "compute",
        domain: members[0].domain,
        region: members[0].region,
        zone: members[0].zone,
        cluster,
        rack,
        summary: `Aggregated ${members.length} GPU objects.`,
        tags: sharedTags,
        updatedAt: latestUpdatedAt,
        metrics: {
          aggregatedObjects: members.length,
          abnormalObjects: members.filter((node) => node.status === "abnormal").length,
          impactedObjects: members.filter((node) => node.status === "impacted").length,
        },
        attributes: {
          syntheticKind: SYNTHETIC_GPU_AGGREGATE_KIND,
          aggregateGroupId,
          aggregateCount: members.length,
          aggregateHostId: hostNodeId,
          aggregateRawType: "gpu",
          aggregateMemberIds: members.map((node) => node.id),
          aggregateExpanded: false,
          fixedMemberIds: fixedMembers.map((node) => node.id),
        },
      });
      return;
    }

    const fixedMembers = members.filter(
      (node) => node.status !== "healthy" || priorityNodeIds.has(node.id),
    );
    const hiddenMembers = members.filter((node) => !fixedMembers.includes(node));

    if (
      hiddenMembers.length < MODIFIED_SERVICE_AGGREGATE_MIN_MEMBERS ||
      expandedAggregateIds.has(aggregateGroupId)
    ) {
      return;
    }

    const serviceId = getAggregateOwnerServiceId(aggregateGroupId);
    const serviceNode = serviceId ? nodeMap.get(serviceId) : undefined;
    const hostLabel = serviceNode?.name ?? serviceId ?? "unassigned";
    const rawType = "pod";
    const aggregateName = `${hostLabel} \u00b7 ${hiddenMembers.length} Pods`;
    const aggregateStatus = getHighestStatus(hiddenMembers);
    const latestUpdatedAt = hiddenMembers
      .map((node) => node.updatedAt)
      .sort((left, right) => right.localeCompare(left))[0] ?? response.lastUpdated;
    const sharedTags = unique(hiddenMembers.flatMap((node) => node.tags));
    const cluster = hiddenMembers.find((node) => Boolean(node.cluster))?.cluster;
    const rack = hiddenMembers.find((node) => Boolean(node.rack))?.rack;

    hiddenMembers.forEach((node) => {
      replacementMap.set(node.id, aggregateGroupId);
    });

    aggregateNodes.push({
      id: aggregateGroupId,
      name: aggregateName,
      type: "pod",
      status: aggregateStatus,
      layer: "service",
      domain: hiddenMembers[0].domain,
      region: hiddenMembers[0].region,
      zone: hiddenMembers[0].zone,
      cluster,
      rack,
      summary: `Aggregated ${hiddenMembers.length} Pod objects.`,
      tags: sharedTags,
      updatedAt: latestUpdatedAt,
      metrics: {
        aggregatedObjects: hiddenMembers.length,
        abnormalObjects: hiddenMembers.filter((node) => node.status === "abnormal").length,
        impactedObjects: hiddenMembers.filter((node) => node.status === "impacted").length,
      },
      attributes: {
        syntheticKind: SYNTHETIC_SERVICE_AGGREGATE_KIND,
        aggregateGroupId,
        aggregateCount: hiddenMembers.length,
        aggregateHostId: serviceId,
        aggregateRawType: rawType,
        aggregateMemberIds: hiddenMembers.map((node) => node.id),
        aggregateExpanded: false,
      },
    });
  });

  const visibleNodes = response.nodes.filter((node) => !replacementMap.has(node.id));
  const mergedEdges = new Map<
    string,
    TopologyRelation & {
      __count: number;
      __syntheticEndpoint: boolean;
      __relationLabels: Set<string>;
    }
  >();

  response.edges.forEach((edge) => {
    const source = replacementMap.get(edge.source) ?? edge.source;
    const target = replacementMap.get(edge.target) ?? edge.target;
    if (source === target) {
      return;
    }

    const key = `${source}:${target}`;
    const syntheticEndpoint =
      source.startsWith("aggregate:") || target.startsWith("aggregate:");
    const existing = mergedEdges.get(key);

    if (!existing) {
      mergedEdges.set(key, {
        ...edge,
        id: syntheticEndpoint ? `aggregated-${key}` : edge.id,
        source,
        target,
        relationType: syntheticEndpoint ? "aggregated" : edge.relationType,
        label: syntheticEndpoint
          ? buildAggregatedRelationLabel(new Set([getRelationLabel(edge)]), 1)
          : edge.label,
        isAggregated: syntheticEndpoint || edge.isAggregated,
        __count: 1,
        __syntheticEndpoint: syntheticEndpoint,
        __relationLabels: new Set([getRelationLabel(edge)]),
      });
      return;
    }

    existing.__count += 1;
    existing.__syntheticEndpoint = existing.__syntheticEndpoint || syntheticEndpoint;
    existing.__relationLabels.add(getRelationLabel(edge));
    existing.status =
      statusRank(edge.status) > statusRank(existing.status)
        ? edge.status
        : existing.status;
    existing.impactLevel =
      impactRank(edge.impactLevel) > impactRank(existing.impactLevel)
        ? edge.impactLevel
        : existing.impactLevel;
    existing.isCritical = existing.isCritical || edge.isCritical;
    existing.isAggregated = true;
    existing.relationType = "aggregated";
  });

  const edges = Array.from(mergedEdges.values()).map(
    ({ __count, __syntheticEndpoint, __relationLabels, ...edge }) => ({
      ...edge,
      label:
        __count > 1 || __syntheticEndpoint
          ? buildAggregatedRelationLabel(__relationLabels, __count)
          : edge.label ?? edge.relationType,
      isAggregated: edge.isAggregated || __count > 1 || __syntheticEndpoint,
    }),
  );
  return {
    ...response,
    nodes: [...visibleNodes, ...aggregateNodes],
    edges,
  };
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

export function getSummaryMetrics(
  response?: TopologyExplorerResponse,
): TopologySummaryMetrics {
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
    abnormalEntities: response.nodes.filter(
      (node) => node.status === "abnormal",
    ).length,
    impactedEntities: response.nodes.filter(
      (node) => node.status === "impacted",
    ).length,
    activePaths: response.paths.filter((path) => path.status === "active")
      .length,
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

export function getNeighborDepths(
  edges: TopologyRelation[],
  nodeId?: string,
  maxDepth = 2,
) {
  if (!nodeId) {
    return new Map<string, number>();
  }

  const undirected = getUndirected(edges);
  const visited = new Map<string, number>([[nodeId, 0]]);
  const queue: Array<{ id: string; depth: number }> = [
    { id: nodeId, depth: 0 },
  ];

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
  const abnormalIds = new Set(
    response.nodes
      .filter((node) => node.status === "abnormal")
      .map((node) => node.id),
  );
  const impactedIds = new Set(
    response.nodes
      .filter((node) => node.status === "impacted")
      .map((node) => node.id),
  );
  const activePathIds = collectPathContext(
    response,
    (path) => path.status === "active",
  );
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
      (impactedIds.has(path.entryNodeId) ||
        path.affectedNodeIds.some((id) => impactedIds.has(id))),
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

function filterByLayer(
  nodes: TopologyObject[],
  layerFilter: ExplorerLayerFilter,
) {
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
  const outgoing = getOutgoing(
    response.edges.filter(
      (edge) => contextIds.has(edge.source) && contextIds.has(edge.target),
    ),
  );
  const nodeMap = toLookupMap(response.nodes);
  const directKeys = new Set(
    response.edges
      .filter(
        (edge) =>
          visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
      )
      .map((edge) => `${edge.source}:${edge.target}`),
  );
  const aggregated = new Map<
    string,
    TopologyRelation & {
      __count: number;
      __relationLabels: Set<string>;
    }
  >();

  visibleNodeIds.forEach((sourceId) => {
    const initialEdges = (outgoing.get(sourceId) ?? []).filter(
      (edge) => !visibleNodeIds.has(edge.target),
    );
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

          const pathStatus = traversedEdges.reduce<TopologyObjectStatus>(
            (winner, edge) => {
              return statusRank(edge.status) > statusRank(winner)
                ? edge.status
                : winner;
            },
            "healthy",
          );
          const withNodeStatus = [sourceId, nextEdge.target]
            .map((id) => nodeMap.get(id)?.status)
            .filter((status): status is TopologyObjectStatus => Boolean(status))
            .reduce<TopologyObjectStatus>(
              (winner, status) =>
                statusRank(status) > statusRank(winner) ? status : winner,
              pathStatus,
            );
          const impactLevel = traversedEdges.reduce<
            TopologyRelation["impactLevel"]
          >((winner, edge) => {
            return impactRank(edge.impactLevel) > impactRank(winner)
              ? edge.impactLevel
              : winner;
          }, "low");
          const relationLabels = new Set(
            traversedEdges.map((traversedEdge) => getRelationLabel(traversedEdge)),
          );

          const existing = aggregated.get(aggregateKey);
          if (!existing) {
            aggregated.set(aggregateKey, {
              id: `aggregated-${sourceId}-${nextEdge.target}`,
              source: sourceId,
              target: nextEdge.target,
              relationType: "aggregated",
              status: withNodeStatus,
              isCritical: traversedEdges.some((edge) => edge.isCritical),
              impactLevel,
              label: buildAggregatedRelationLabel(relationLabels, 1),
              isAggregated: true,
              __count: 1,
              __relationLabels: relationLabels,
            });
            return;
          }

          existing.__count += 1;
          relationLabels.forEach((label) => existing.__relationLabels.add(label));
          existing.status =
            statusRank(withNodeStatus) > statusRank(existing.status)
              ? withNodeStatus
              : existing.status;
          existing.impactLevel =
            impactRank(impactLevel) > impactRank(existing.impactLevel)
              ? impactLevel
              : existing.impactLevel;
          existing.isCritical =
            existing.isCritical || traversedEdges.some((edge) => edge.isCritical);
          existing.label = buildAggregatedRelationLabel(
            existing.__relationLabels,
            existing.__count,
          );
          return;
        }

        if (current.visitedHiddenIds.has(nextEdge.target)) {
          return;
        }

        queue.push({
          currentId: nextEdge.target,
          traversedEdges,
          visitedHiddenIds: new Set(current.visitedHiddenIds).add(
            nextEdge.target,
          ),
        });
      });
    }
  });

  return Array.from(aggregated.values()).map(
    ({ __count: _count, __relationLabels: _labels, ...edge }) => edge,
  );
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

  const contextIds = getFilterContextIds(
    response,
    filters.statusFilter,
    filters.summaryFilter,
  );
  const visibleNodes = filterByLayer(
    response.nodes.filter((node) => contextIds.has(node.id)),
    filters.layerFilter,
  );
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const directEdges = response.edges.filter(
    (edge) =>
      visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
  );
  const aggregatedEdges =
    filters.layerFilter === "all"
      ? []
      : buildAggregatedEdges(response, visibleNodeIds, contextIds);

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

export function getImpactTopology(
  response: TopologyExplorerResponse | undefined,
  pathId?: string,
): ImpactTopology | null {
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
  const nodeIds = collectPathContext(
    response,
    (path) => path.id === activePath.id,
  );
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

export function getRelationsForNode(
  response: TopologyExplorerResponse | undefined,
  nodeId?: string,
) {
  if (!response || !nodeId) {
    return {
      upstream: [] as TopologyObject[],
      downstream: [] as TopologyObject[],
      neighbors: [] as TopologyObject[],
    };
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

export function getAffectedObjectsForNode(
  response: TopologyExplorerResponse | undefined,
  nodeId?: string,
) {
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

export function buildTopologyTree(
  response: TopologyExplorerResponse | undefined,
): TopologyTreeNode | null {
  if (!response) {
    return null;
  }

  const racks = response.nodes.filter((node) => node.type === "rack");
  const nodeMap = toLookupMap(response.nodes);
  const containsEdges = response.edges.filter(
    (edge) => edge.relationType === "contains",
  );
  const serviceEdges = response.edges.filter(
    (edge) => edge.relationType === "runs_on",
  );

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

        const bmcEndpoints = containsEdges
          .filter((edge) => edge.target === node.id)
          .map((edge) => nodeMap.get(edge.source))
          .filter((bmc): bmc is TopologyObject => bmc?.type === "bmc")
          .map<TopologyTreeNode>((bmc) => ({
            id: bmc.id,
            label: bmc.name,
            type: "object",
            objectId: bmc.id,
            objectType: bmc.type,
          }));

        return {
          id: node.id,
          label: node.name,
          type: "object",
          objectId: node.id,
          objectType: node.type,
          children: [...bmcEndpoints, ...gpus, ...services],
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
        .filter(
          (node) => node.type === "service" && node.cluster === cluster.id,
        )
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
        label: "Clusters / Services",
        type: "group",
        children: clusterNodes,
      },
      {
        id: `${response.site.id}-network`,
        label: "Network / Switches / Ports",
        type: "group",
        children: switchNodes,
      },
    ],
  };
}

export type TopologyStageFilters = {
  layerFilter: ExplorerLayerFilter;
  searchQuery: string;
  searchResultIds?: string[];
};

export type ModifiedTopologyStageOptions = {
  expandedAggregateIds?: string[];
  priorityNodeIds?: string[];
};
export type ObjectTopologyDetail = {
  focalNode?: TopologyObject;
  nodes: TopologyObject[];
  edges: TopologyRelation[];
  upstream: TopologyObject[];
  downstream: TopologyObject[];
  neighbors: TopologyObject[];
  paths: TopologyPath[];
  affectedObjects: TopologyObject[];
  notFound: boolean;
};

function getDirectNeighborContextIds(
  response: TopologyExplorerResponse,
  focalNodeIds: Set<string>,
) {
  const contextIds = new Set(focalNodeIds);

  response.edges.forEach((edge) => {
    if (focalNodeIds.has(edge.source) || focalNodeIds.has(edge.target)) {
      contextIds.add(edge.source);
      contextIds.add(edge.target);
    }
  });

  return contextIds;
}

function filterEssentialTopologyEdges(nodes: TopologyObject[], edges: TopologyRelation[]) {
  const nodeMap = toLookupMap(nodes);

  // Use a canonical (sorted) pair key so the filter is direction-agnostic.
  const allowedPairs = new Set([
    "cluster:switch",
    "node:port",
    "gpu:node",
    "bmc:node",
    "node:service",
    "pod:service",
    "port:switch",
  ]);

  function isAllowedPair(left: TopologyObject | undefined, right: TopologyObject | undefined) {
    if (!left || !right) {
      return false;
    }
    const key = [left.type, right.type].sort((a, b) => a.localeCompare(b)).join(":");
    return allowedPairs.has(key);
  }

  const relationPriority: Record<TopologyRelation["relationType"], number> = {
    connects_to: 6,
    uplink_to: 5,
    runs_on: 4,
    depends_on: 3,
    contains: 2,
    aggregated: 1,
  };

  const bestByPair = new Map<string, TopologyRelation>();
  edges.forEach((edge) => {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (!isAllowedPair(source, target)) {
      return;
    }

    const pairKey = edge.source < edge.target ? `${edge.source}::${edge.target}` : `${edge.target}::${edge.source}`;
    const existing = bestByPair.get(pairKey);
    if (!existing) {
      bestByPair.set(pairKey, edge);
      return;
    }

    const existingScore =
      (existing.isCritical ? 100 : 0) + (relationPriority[existing.relationType] ?? 0);
    const candidateScore =
      (edge.isCritical ? 100 : 0) + (relationPriority[edge.relationType] ?? 0);
    if (candidateScore > existingScore) {
      bestByPair.set(pairKey, edge);
    }
  });

  return Array.from(bestByPair.values());
}

function getAggregateOwnerServiceId(aggregateGroupId: string) {
  const prefix = "aggregate:";
  const suffix = ":pod";
  if (!aggregateGroupId.startsWith(prefix) || !aggregateGroupId.endsWith(suffix)) {
    return undefined;
  }
  const value = aggregateGroupId.slice(prefix.length, -suffix.length);
  return value.trim() ? value : undefined;
}

function getGpuHostNodeId(
  gpu: TopologyObject,
  nodeMap: Map<string, TopologyObject>,
  outgoing: Map<string, TopologyRelation[]>,
  incoming: Map<string, TopologyRelation[]>,
) {
  const outgoingEdge = (outgoing.get(gpu.id) ?? []).find((edge) => edge.relationType === "contains");
  const outgoingTarget = outgoingEdge ? nodeMap.get(outgoingEdge.target) : undefined;
  if (outgoingTarget?.type === "node") {
    return outgoingTarget.id;
  }

  const incomingEdge = (incoming.get(gpu.id) ?? []).find((edge) => edge.relationType === "contains");
  const incomingSource = incomingEdge ? nodeMap.get(incomingEdge.source) : undefined;
  if (incomingSource?.type === "node") {
    return incomingSource.id;
  }

  const fallbackHost = typeof gpu.attributes.host === "string" ? gpu.attributes.host : undefined;
  return fallbackHost;
}

export function getStageTopology(
  response: TopologyExplorerResponse | undefined,
  filters: TopologyStageFilters,
) {
  const scopedResponse = getGlobalTopologyDisplayData(response);

  if (!scopedResponse) {
    return {
      nodes: [] as TopologyObject[],
      edges: [] as TopologyRelation[],
      contextIds: new Set<string>(),
      visibleNodeIds: new Set<string>(),
      searchResultIds: [] as string[],
    };
  }

  const searchResultIds =
    filters.searchResultIds ??
    searchTopologyObjects(
      filters.layerFilter === "all"
        ? scopedResponse.nodes
        : scopedResponse.nodes.filter((node) => node.layer === filters.layerFilter),
      filters.searchQuery,
    ).map((node) => node.id);
  const contextIds = filters.searchQuery.trim()
    ? getDirectNeighborContextIds(scopedResponse, new Set(searchResultIds))
    : new Set(scopedResponse.nodes.map((node) => node.id));
  const visibleNodes = filterByLayer(
    scopedResponse.nodes.filter((node) => contextIds.has(node.id)),
    filters.layerFilter,
  );
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const directEdges = scopedResponse.edges.filter(
    (edge) =>
      visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
  );
  const aggregatedEdges =
    filters.layerFilter === "all"
      ? []
      : buildAggregatedEdges(scopedResponse, visibleNodeIds, contextIds);

  return {
    nodes: visibleNodes,
    edges: [...directEdges, ...aggregatedEdges],
    contextIds,
    visibleNodeIds,
    searchResultIds,
  };
}

export function getModifiedSearchResultIds(
  response: TopologyExplorerResponse | undefined,
  layerFilter: ExplorerLayerFilter,
  searchQuery: string,
) {
  if (!response) {
    return [];
  }

  const searchNodes =
    layerFilter === "all"
      ? response.nodes
      : response.nodes.filter((node) => node.layer === layerFilter);

  return searchTopologyObjects(searchNodes, searchQuery)
    .filter((node) => !isSyntheticTopologyNode(node))
    .map((node) => node.id);
}

export function getModifiedStageTopology(
  response: TopologyExplorerResponse | undefined,
  filters: TopologyStageFilters,
  options: ModifiedTopologyStageOptions = {},
) {
  if (!response) {
    return {
      nodes: [] as TopologyObject[],
      edges: [] as TopologyRelation[],
      contextIds: new Set<string>(),
      visibleNodeIds: new Set<string>(),
      searchResultIds: [] as string[],
    };
  }

  const searchResultIds =
    filters.searchResultIds ??
    getModifiedSearchResultIds(response, filters.layerFilter, filters.searchQuery);
  const priorityNodeIds = unique([
    ...searchResultIds,
    ...(options.priorityNodeIds ?? []),
  ]);
  const aggregatedResponse = buildModifiedAggregatedTopology(response, {
    expandedAggregateIds: options.expandedAggregateIds,
    priorityNodeIds,
  });
  const contextIds = filters.searchQuery.trim()
    ? getDirectNeighborContextIds(aggregatedResponse, new Set(searchResultIds))
    : new Set(aggregatedResponse.nodes.map((node) => node.id));
  const visibleNodes = filterByLayer(
    aggregatedResponse.nodes.filter((node) => contextIds.has(node.id)),
    filters.layerFilter,
  );
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const directEdges = aggregatedResponse.edges.filter(
    (edge) =>
      visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
  );
  const essentialEdges = filterEssentialTopologyEdges(visibleNodes, directEdges);
  const aggregatedEdges =
    filters.layerFilter === "all"
      ? []
      : buildAggregatedEdges(aggregatedResponse, visibleNodeIds, contextIds);

  return {
    nodes: visibleNodes,
    edges: [...essentialEdges, ...aggregatedEdges],
    contextIds,
    visibleNodeIds,
    searchResultIds,
  };
}
export function getObjectTopologyDetail(
  response: TopologyExplorerResponse | undefined,
  nodeId?: string,
): ObjectTopologyDetail {
  if (!response || !nodeId) {
    return {
      focalNode: undefined,
      nodes: [],
      edges: [],
      upstream: [],
      downstream: [],
      neighbors: [],
      paths: [],
      affectedObjects: [],
      notFound: false,
    };
  }

  const nodeMap = toLookupMap(response.nodes);
  const focalNode = nodeMap.get(nodeId);
  if (!focalNode) {
    return {
      focalNode: undefined,
      nodes: [],
      edges: [],
      upstream: [],
      downstream: [],
      neighbors: [],
      paths: [],
      affectedObjects: [],
      notFound: true,
    };
  }

  const { upstream, downstream, neighbors } = getRelationsForNode(
    response,
    nodeId,
  );
  const contextIds = new Set<string>([
    nodeId,
    ...upstream.map((node) => node.id),
    ...downstream.map((node) => node.id),
  ]);

  return {
    focalNode,
    nodes: response.nodes.filter((node) => contextIds.has(node.id)),
    edges: response.edges.filter(
      (edge) => edge.source === nodeId || edge.target === nodeId,
    ),
    upstream,
    downstream,
    neighbors,
    paths: getPathsForNode(response.paths, nodeId),
    affectedObjects: getAffectedObjectsForNode(response, nodeId),
    notFound: false,
  };
}
