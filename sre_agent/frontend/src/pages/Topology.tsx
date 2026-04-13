import { useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";

import type { TopologyObject } from "../api/types";
import type { TopologyCanvasHandle } from "../features/topologyExplorer/components/TopologyCanvas";
import TopologyExplorer from "../features/topologyExplorer/components/TopologyExplorer";
import {
  buildTopologyTree,
  getGlobalTopologyDisplayData,
  getNeighborDepths,
  getStageTopology,
} from "../features/topologyExplorer/selectors";
import { useTopologyExplorerStore } from "../features/topologyExplorer/store";
import "../features/topologyExplorer/topologyExplorer.css";

function TopologyPage() {
  const canvasRef = useRef<TopologyCanvasHandle | null>(null);
  const navigate = useNavigate();
  const {
    data,
    isLoading,
    error,
    viewMode,
    selectedNodeId,
    searchQuery,
    searchResultIds,
    searchFeedback,
    layerFilter,
    legendOpen,
    filterPanelOpen,
    layoutPreset,
    hoveredNodeId,
    fetchTopologyExplorer,
    setViewMode,
    setSelectedNodeId,
    setSearchQuery,
    focusFirstSearchResult,
    setLayerFilter,
    setLegendOpen,
    setFilterPanelOpen,
    cycleLayoutPreset,
    setHoveredNodeId,
    resetExplorerView,
  } = useTopologyExplorerStore();

  useEffect(() => {
    void fetchTopologyExplorer();
  }, [fetchTopologyExplorer]);

  const topologyPageData = useMemo(() => getGlobalTopologyDisplayData(data), [data]);
  const effectiveSelectedNodeId =
    selectedNodeId && topologyPageData?.nodes.some((node) => node.id === selectedNodeId)
      ? selectedNodeId
      : undefined;
  const stageTopology = useMemo(
    () =>
      getStageTopology(topologyPageData, {
        layerFilter,
        searchQuery,
        searchResultIds,
      }),
    [topologyPageData, layerFilter, searchQuery, searchResultIds],
  );
  const tree = useMemo(() => buildTopologyTree(topologyPageData), [topologyPageData]);
  const selectedNode = useMemo(
    () => topologyPageData?.nodes.find((node) => node.id === effectiveSelectedNodeId),
    [topologyPageData, effectiveSelectedNodeId],
  );
  const searchResultNodes = useMemo(() => {
    const nodeMap = new Map(topologyPageData?.nodes.map((node) => [node.id, node]) ?? []);
    return searchResultIds
      .map((id) => nodeMap.get(id))
      .filter((node): node is TopologyObject => Boolean(node));
  }, [topologyPageData, searchResultIds]);
  const neighborDepths = useMemo(
    () => getNeighborDepths(stageTopology.edges, effectiveSelectedNodeId, 1),
    [stageTopology.edges, effectiveSelectedNodeId],
  );

  const focusNode = (nodeId: string) => {
    window.requestAnimationFrame(() => {
      canvasRef.current?.focusNode(nodeId);
    });
  };

  const handleSearchSubmit = () => {
    const targetId = focusFirstSearchResult();
    if (!targetId) {
      return;
    }

    setViewMode("graph");
    focusNode(targetId);
  };

  const handleSearchResultSelect = (nodeId: string) => {
    setSelectedNodeId(nodeId);
    setViewMode("graph");
    focusNode(nodeId);
  };

  const handleOpenObjectTopology = (nodeId: string, mode: "default" | "isolate") => {
    const query = mode === "isolate" ? "?mode=isolate" : "";
    navigate(`/topology/object/${nodeId}${query}`);
  };

  return (
    <div className="page-grid topology-modified-page">
      <h1 className="visually-hidden">运行拓扑</h1>
      <section className="page-stage topology-modified-stage topology-modified-stage--canvas-only">
        <TopologyExplorer
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
          legendOpen={legendOpen}
          matchedNodeIds={searchResultIds}
          neighborDepths={neighborDepths}
          onCycleLayoutPreset={() => {
            cycleLayoutPreset();
            window.requestAnimationFrame(() => canvasRef.current?.fitView());
          }}
          onFilterPanelOpenChange={setFilterPanelOpen}
          onFitCanvas={() => canvasRef.current?.fitView()}
          onHoverNode={setHoveredNodeId}
          onLayerFilterChange={setLayerFilter}
          onOpenObjectTopology={handleOpenObjectTopology}
          onRecenter={() => canvasRef.current?.recenter(effectiveSelectedNodeId)}
          onResetView={() => {
            resetExplorerView();
            window.requestAnimationFrame(() => canvasRef.current?.fitView());
          }}
          onSearchQueryChange={setSearchQuery}
          onSearchResultSelect={handleSearchResultSelect}
          onSearchSubmit={handleSearchSubmit}
          onSelectNode={setSelectedNodeId}
          onToggleLegend={() => setLegendOpen(!legendOpen)}
          onViewModeChange={setViewMode}
          onZoomIn={() => canvasRef.current?.zoomIn()}
          onZoomOut={() => canvasRef.current?.zoomOut()}
          searchFeedback={searchFeedback}
          searchQuery={searchQuery}
          searchResultNodes={searchResultNodes}
          selectedNode={selectedNode}
          selectedNodeId={effectiveSelectedNodeId}
          tree={tree}
          viewMode={viewMode === "impact" ? "graph" : viewMode}
        />
      </section>
    </div>
  );
}

export default TopologyPage;
