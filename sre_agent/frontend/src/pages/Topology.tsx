import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { TopologyExplorerResponse, TopologyObject } from "../api/types";
import type { TopologyCanvasHandle } from "../features/topologyExplorer/components/TopologyCanvas";
import TopologyExplorer from "../features/topologyExplorer/components/TopologyExplorer";
import {
  buildTopologyTree,
  getGlobalTopologyDisplayData,
  getModifiedSearchResultIds,
  getModifiedStageTopology,
  getUpDownstreamChainDepths,
  getStageTopology,
} from "../features/topologyExplorer/selectors";
import { useTopologyExplorerStore } from "../features/topologyExplorer/store";
import type { SearchFeedback } from "../features/topologyExplorer/types";
import {
  buildTopologyObjectPath,
} from "../features/topologyExplorer/topologyObjectRoute";
import "../features/topologyExplorer/topologyExplorer.css";

export type TopologyPageVariant = "default" | "modified";

type TopologyPageProps = {
  variant?: TopologyPageVariant;
};

function resolveSearchFeedback(searchQuery: string, searchResultIds: string[]): SearchFeedback {
  if (!searchQuery.trim()) {
    return "idle";
  }

  return searchResultIds.length > 0 ? "ready" : "not_found";
}

type ExpandedAggregateMeta = {
  aggregateId: string;
  label: string;
  memberIds: string[];
};

type PodAggregateResolution = {
  aggregateId: string;
  label: string;
  memberIds: string[];
};

function isNamespaceGroupNode(node: TopologyObject | undefined): node is TopologyObject {
  return node?.type === "service" && String(node.attributes.kind ?? "").toLowerCase() === "namespace_group";
}

function isPodNode(node: TopologyObject | undefined): node is TopologyObject {
  if (!node) {
    return false;
  }
  return node.type === "pod" || String(node.attributes.rawType ?? "").trim().toLowerCase() === "pod";
}

function resolvePodAggregateOwnerId(
  data: TopologyExplorerResponse,
  podId: string,
  nodeMap: Map<string, TopologyObject>,
) {
  const pod = nodeMap.get(podId);
  if (!isPodNode(pod)) {
    return undefined;
  }

  const namespaceFromAttributes =
    typeof pod.attributes.namespace === "string" && pod.attributes.namespace.trim()
      ? pod.attributes.namespace.trim()
      : undefined;
  const namespaceNodeById =
    namespaceFromAttributes ? nodeMap.get(`ns:${namespaceFromAttributes}`) : undefined;
  if (isNamespaceGroupNode(namespaceNodeById)) {
    return namespaceNodeById.id;
  }

  const namespaceNodeFromEdges = data.edges
    .filter((edge) => edge.source === podId || edge.target === podId)
    .map((edge) => (edge.source === podId ? nodeMap.get(edge.target) : nodeMap.get(edge.source)))
    .find((candidate) => isNamespaceGroupNode(candidate));

  if (isNamespaceGroupNode(namespaceNodeFromEdges)) {
    return namespaceNodeFromEdges.id;
  }

  if (namespaceFromAttributes) {
    return `ns:${namespaceFromAttributes}`;
  }

  return "unassigned";
}

function resolvePodAggregateForNode(
  data: TopologyExplorerResponse | undefined,
  nodeId: string,
): PodAggregateResolution | undefined {
  if (!data) {
    return undefined;
  }
  const nodeMap = new Map(data.nodes.map((node) => [node.id, node]));
  const targetNode = nodeMap.get(nodeId);
  if (!isPodNode(targetNode)) {
    return undefined;
  }

  const ownerId = resolvePodAggregateOwnerId(data, nodeId, nodeMap);
  if (!ownerId) {
    return undefined;
  }

  const memberIds = data.nodes
    .filter((node) => isPodNode(node))
    .filter((node) => resolvePodAggregateOwnerId(data, node.id, nodeMap) === ownerId)
    .map((node) => node.id);
  if (memberIds.length < 2) {
    return undefined;
  }

  const namespaceNode = nodeMap.get(ownerId);
  const label = `${namespaceNode?.name ?? ownerId} · ${memberIds.length} Pods`;
  return {
    aggregateId: `aggregate:${ownerId}:pod`,
    label,
    memberIds,
  };
}

function TopologyPage({ variant = "modified" }: TopologyPageProps) {
  const canvasRef = useRef<TopologyCanvasHandle | null>(null);
  const navigate = useNavigate();
  const [expandedAggregateIds, setExpandedAggregateIds] = useState<string[]>([]);
  const [expandedAggregateMeta, setExpandedAggregateMeta] = useState<Record<string, ExpandedAggregateMeta>>({});
  const isModifiedVariant = variant === "modified";
  const {
    data,
    isLoading,
    error,
    syncState,
    lastError,
    scopeMode,
    selectedRoomId,
    viewMode,
    selectedNodeId,
    searchQuery,
    searchResultIds,
    searchFeedback,
    layerFilter,
    secondaryPanelOpen,
    filterPanelOpen,
    layoutPreset,
    hoveredNodeId,
    fetchTopologyExplorer,
    setScopeMode,
    setSelectedRoomId,
    setViewMode,
    setSelectedNodeId,
    setSearchQuery,
    focusFirstSearchResult,
    setLayerFilter,
    setSecondaryPanelOpen,
    setFilterPanelOpen,
    setLayoutPreset,
    setHoveredNodeId,
    resetExplorerView,
  } = useTopologyExplorerStore();

  useEffect(() => {
    void fetchTopologyExplorer();
  }, [fetchTopologyExplorer]);

  useEffect(() => {
    if (!isModifiedVariant) {
      return;
    }

    setExpandedAggregateIds([]);
    setExpandedAggregateMeta({});
  }, [data?.lastUpdated, isModifiedVariant]);

  const topologyPageData = useMemo(
    () => (isModifiedVariant ? data : getGlobalTopologyDisplayData(data)),
    [data, isModifiedVariant],
  );

  const pageSearchResultIds = useMemo(
    () =>
      isModifiedVariant
        ? getModifiedSearchResultIds(data, layerFilter, searchQuery)
        : searchResultIds,
    [data, isModifiedVariant, layerFilter, searchQuery, searchResultIds],
  );

  const pageSearchFeedback = isModifiedVariant
    ? resolveSearchFeedback(searchQuery, pageSearchResultIds)
    : searchFeedback;

  const stageTopology = useMemo(
    () =>
      isModifiedVariant
        ? getModifiedStageTopology(
            data,
            {
              layerFilter,
              searchQuery,
              searchResultIds: pageSearchResultIds,
            },
            {
              expandedAggregateIds,
              priorityNodeIds: selectedNodeId ? [selectedNodeId] : [],
            },
          )
        : getStageTopology(topologyPageData, {
            layerFilter,
            searchQuery,
            searchResultIds: pageSearchResultIds,
          }),
    [
      data,
      expandedAggregateIds,
      isModifiedVariant,
      layerFilter,
      pageSearchResultIds,
      searchQuery,
      selectedNodeId,
      topologyPageData,
    ],
  );

  const effectiveSelectedNodeId =
    selectedNodeId && stageTopology.nodes.some((node) => node.id === selectedNodeId)
      ? selectedNodeId
      : selectedNodeId && topologyPageData?.nodes.some((node) => node.id === selectedNodeId)
        ? selectedNodeId
        : undefined;

  const tree = useMemo(() => buildTopologyTree(topologyPageData), [topologyPageData]);

  const selectedNode = useMemo(() => {
    if (!effectiveSelectedNodeId) {
      return undefined;
    }

    return (
      stageTopology.nodes.find((node) => node.id === effectiveSelectedNodeId) ??
      topologyPageData?.nodes.find((node) => node.id === effectiveSelectedNodeId)
    );
  }, [effectiveSelectedNodeId, stageTopology.nodes, topologyPageData]);

  const searchResultNodes = useMemo(() => {
    const nodeMap = new Map((topologyPageData?.nodes ?? []).map((node) => [node.id, node]));
    return pageSearchResultIds
      .map((id) => nodeMap.get(id))
      .filter((node): node is TopologyObject => Boolean(node));
  }, [pageSearchResultIds, topologyPageData]);

  const neighborDepths = useMemo(
    () => getUpDownstreamChainDepths(stageTopology.edges, effectiveSelectedNodeId),
    [stageTopology.edges, effectiveSelectedNodeId],
  );

  const focusNode = (nodeId: string) => {
    window.requestAnimationFrame(() => {
      canvasRef.current?.focusNode(nodeId);
    });
  };

  const handleSearchSubmit = () => {
    const targetId = isModifiedVariant ? pageSearchResultIds[0] : focusFirstSearchResult();
    if (!targetId) {
      return;
    }

    if (isModifiedVariant) {
      const aggregate = resolvePodAggregateForNode(topologyPageData, targetId);
      if (aggregate) {
        setExpandedAggregateIds((current) =>
          current.includes(aggregate.aggregateId) ? current : [...current, aggregate.aggregateId],
        );
        setExpandedAggregateMeta((current) => ({
          ...current,
          [aggregate.aggregateId]: aggregate,
        }));
      }
      setSelectedNodeId(targetId);
    }

    setViewMode("graph");
    focusNode(targetId);
  };

  const handleSearchResultSelect = (nodeId: string) => {
    if (isModifiedVariant) {
      const aggregate = resolvePodAggregateForNode(topologyPageData, nodeId);
      if (aggregate) {
        setExpandedAggregateIds((current) =>
          current.includes(aggregate.aggregateId) ? current : [...current, aggregate.aggregateId],
        );
        setExpandedAggregateMeta((current) => ({
          ...current,
          [aggregate.aggregateId]: aggregate,
        }));
      }
    }
    setSelectedNodeId(nodeId);
    setViewMode("graph");
    focusNode(nodeId);
  };

  const handleToggleAggregateNode = (aggregateId: string) => {
    const isExpanded = expandedAggregateIds.includes(aggregateId);
    const aggregateNode = stageTopology.nodes.find((node) => node.id === aggregateId);
    const memberIds = Array.isArray(aggregateNode?.attributes?.aggregateMemberIds)
      ? (aggregateNode?.attributes?.aggregateMemberIds as string[])
      : [];
    const focusTargetId = !isExpanded ? memberIds[0] : undefined;

    setExpandedAggregateIds((current) =>
      current.includes(aggregateId)
        ? current.filter((candidate) => candidate !== aggregateId)
        : [...current, aggregateId],
    );

    setExpandedAggregateMeta((current) => {
      if (isExpanded) {
        const { [aggregateId]: _removed, ...rest } = current;
        return rest;
      }
      if (!aggregateNode || memberIds.length === 0) {
        return current;
      }
      return {
        ...current,
        [aggregateId]: {
          aggregateId,
          label: aggregateNode.name,
          memberIds,
        },
      };
    });

    if (focusTargetId) {
      setSelectedNodeId(focusTargetId);
      setViewMode("graph");
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          canvasRef.current?.focusNode(focusTargetId);
        });
      });
    }
  };

  const handleOpenObjectTopology = (nodeId: string, mode: "default" | "isolate") => {
    navigate(buildTopologyObjectPath(nodeId, mode));
  };

  return (
    <div className={`page-grid topology-modified-page topology-route topology-route--${variant}`}>
      <h1 className="visually-hidden">拓扑</h1>
      <section className="page-stage topology-modified-stage topology-modified-stage--canvas-only">
        <TopologyExplorer
          allNodes={topologyPageData?.nodes ?? []}
          canvasRef={canvasRef}
          error={error}
          filterPanelOpen={filterPanelOpen}
          lastError={lastError}
          syncState={syncState}
          graphEdges={stageTopology.edges}
          graphNodes={stageTopology.nodes}
          hasSourceData={Boolean(topologyPageData?.nodes?.length)}
          hoveredNodeId={hoveredNodeId}
          isLoading={isLoading}
          layerFilter={layerFilter}
          layoutPreset={layoutPreset}
          matchedNodeIds={pageSearchResultIds}
          neighborDepths={neighborDepths}
          onFilterPanelOpenChange={setFilterPanelOpen}
          onFitCanvas={() => canvasRef.current?.fitView()}
          onHoverNode={setHoveredNodeId}
          onLayerFilterChange={setLayerFilter}
          onLayoutPresetChange={setLayoutPreset}
          onOpenObjectTopology={handleOpenObjectTopology}
          onRecenter={() => canvasRef.current?.recenter(effectiveSelectedNodeId)}
          onResetView={() => {
            resetExplorerView();
            setExpandedAggregateIds([]);
            setExpandedAggregateMeta({});
            window.requestAnimationFrame(() => canvasRef.current?.fitView());
          }}
          onScopeModeChange={setScopeMode}
          onSearchQueryChange={setSearchQuery}
          onSearchResultSelect={handleSearchResultSelect}
          onSearchSubmit={handleSearchSubmit}
          onSecondaryPanelOpenChange={setSecondaryPanelOpen}
          onSelectNode={setSelectedNodeId}
          onSelectedRoomChange={setSelectedRoomId}
          onToggleAggregateNode={isModifiedVariant ? handleToggleAggregateNode : undefined}
          onViewModeChange={setViewMode}
          onZoomIn={() => canvasRef.current?.zoomIn()}
          onZoomOut={() => canvasRef.current?.zoomOut()}
          scopeMode={scopeMode}
          searchFeedback={pageSearchFeedback}
          searchQuery={searchQuery}
          searchResultNodes={searchResultNodes}
          secondaryPanelOpen={secondaryPanelOpen}
          selectedNode={selectedNode}
          selectedNodeId={effectiveSelectedNodeId}
          selectedRoomId={selectedRoomId}
          tree={tree}
          variant={variant}
          viewMode={viewMode === "impact" ? "graph" : viewMode}
          expandedAggregateIds={expandedAggregateIds}
          expandedAggregateMeta={expandedAggregateMeta}
          onCollapseAggregate={isModifiedVariant ? handleToggleAggregateNode : undefined}
        />
      </section>
    </div>
  );
}

export default TopologyPage;


