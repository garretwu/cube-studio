import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { TopologyObject } from "../api/types";
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
  buildModifiedTopologyObjectPath,
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

function TopologyPage({ variant = "default" }: TopologyPageProps) {
  const canvasRef = useRef<TopologyCanvasHandle | null>(null);
  const navigate = useNavigate();
  const [expandedAggregateIds, setExpandedAggregateIds] = useState<string[]>([]);
  const [expandedAggregateMeta, setExpandedAggregateMeta] = useState<Record<string, ExpandedAggregateMeta>>({});
  const isModifiedVariant = variant === "modified";
  const {
    data,
    isLoading,
    error,
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
      setSelectedNodeId(targetId);
    }

    setViewMode("graph");
    focusNode(targetId);
  };

  const handleSearchResultSelect = (nodeId: string) => {
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
    if (isModifiedVariant) {
      navigate(buildModifiedTopologyObjectPath(nodeId, mode));
      return;
    }
    navigate(buildTopologyObjectPath(nodeId, mode));
  };

  return (
    <div className={`page-grid topology-modified-page topology-route topology-route--${variant}`}>
      <h1 className="visually-hidden">{variant === "modified" ? "拓扑（改造）" : "运行拓扑"}</h1>
      <section className="page-stage topology-modified-stage topology-modified-stage--canvas-only">
        <TopologyExplorer
          allNodes={topologyPageData?.nodes ?? []}
          canvasRef={canvasRef}
          error={error}
          filterPanelOpen={filterPanelOpen}
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




