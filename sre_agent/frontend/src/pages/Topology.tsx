import { useEffect, useMemo, useRef } from "react";

import type { TopologyCanvasHandle } from "../features/topologyExplorer/components/TopologyCanvas";
import TopologyExplorer from "../features/topologyExplorer/components/TopologyExplorer";
import TopologyHeader from "../features/topologyExplorer/components/TopologyHeader";
import {
  buildTopologyTree,
  getAffectedObjectsForNode,
  getNeighborDepths,
  getPathsForNode,
  getRelationsForNode,
  getSummaryMetrics,
  getVisibleTopology,
} from "../features/topologyExplorer/selectors";
import { useTopologyExplorerStore } from "../features/topologyExplorer/store";
import "../features/topologyExplorer/topologyExplorer.css";

function TopologyPage() {
  const canvasRef = useRef<TopologyCanvasHandle | null>(null);
  const {
    data,
    isLoading,
    error,
    viewMode,
    selectedNodeId,
    inspectorOpen,
    searchQuery,
    matchedNodeIds,
    hoveredNodeId,
    searchFeedback,
    statusFilter,
    layerFilter,
    summaryFilter,
    legendOpen,
    layoutPreset,
    inspectorTab,
    fetchTopologyExplorer,
    setViewMode,
    setSelectedNodeId,
    setInspectorOpen,
    setSearchQuery,
    locateSearchResult,
    setStatusFilter,
    setLayerFilter,
    applySummaryFilter,
    setLegendOpen,
    cycleLayoutPreset,
    setHoveredNodeId,
    setInspectorTab,
    resetExplorerView,
  } = useTopologyExplorerStore();

  useEffect(() => {
    void fetchTopologyExplorer();
  }, [fetchTopologyExplorer]);

  const summaryMetrics = useMemo(() => getSummaryMetrics(data), [data]);
  const visibleTopology = useMemo(
    () =>
      getVisibleTopology(data, {
        statusFilter,
        layerFilter,
        summaryFilter,
      }),
    [data, layerFilter, statusFilter, summaryFilter],
  );
  const tree = useMemo(() => buildTopologyTree(data), [data]);
  const selectedNode = data?.nodes?.find((node) => node.id === selectedNodeId);
  const neighborDepths = useMemo(() => getNeighborDepths(data?.edges ?? [], selectedNodeId), [data?.edges, selectedNodeId]);
  const relations = useMemo(() => getRelationsForNode(data, selectedNodeId), [data, selectedNodeId]);
  const pathsForSelected = useMemo(() => getPathsForNode(data?.paths ?? [], selectedNodeId), [data?.paths, selectedNodeId]);
  const affectedObjects = useMemo(() => getAffectedObjectsForNode(data, selectedNodeId), [data, selectedNodeId]);

  const focusCurrentSelection = () => {
    if (selectedNodeId) {
      canvasRef.current?.focusNode(selectedNodeId);
      return;
    }

    canvasRef.current?.fitView();
  };

  const handleSearchSubmit = () => {
    const targetId = locateSearchResult();
    if (!targetId) {
      return;
    }

    window.requestAnimationFrame(() => {
      canvasRef.current?.focusNode(targetId);
    });
  };

  const handleSummarySelect = (filter: typeof summaryFilter) => {
    applySummaryFilter(filter);
    window.requestAnimationFrame(() => {
      const state = useTopologyExplorerStore.getState();

      if (state.selectedNodeId) {
        canvasRef.current?.focusNode(state.selectedNodeId);
      } else {
        canvasRef.current?.fitView();
      }
    });
  };

  const handleViewModeChange = (nextViewMode: typeof viewMode) => {
    const resolvedViewMode = nextViewMode === "impact" ? "graph" : nextViewMode;
    setViewMode(resolvedViewMode);

    window.requestAnimationFrame(() => {
      focusCurrentSelection();
    });
  };

  const handleResetView = () => {
    resetExplorerView();
    window.requestAnimationFrame(() => {
      canvasRef.current?.fitView();
    });
  };

  return (
    <div className="page-grid topology-modified-page">
      <TopologyHeader lastUpdated={data?.lastUpdated} />

      <TopologyExplorer
        affectedObjects={affectedObjects}
        canvasRef={canvasRef}
        downstream={relations.downstream}
        error={error}
        graphEdges={visibleTopology.edges}
        graphNodes={visibleTopology.nodes}
        hasSourceData={Boolean(data?.nodes?.length)}
        hoveredNodeId={hoveredNodeId}
        inspectorOpen={inspectorOpen}
        inspectorTab={inspectorTab}
        isLoading={isLoading}
        layerFilter={layerFilter}
        layoutPreset={layoutPreset}
        legendOpen={legendOpen}
        matchedCount={matchedNodeIds.length}
        matchedNodeIds={matchedNodeIds}
        neighborDepths={neighborDepths}
        neighbors={relations.neighbors}
        onCycleLayoutPreset={() => {
          cycleLayoutPreset();
          window.requestAnimationFrame(() => canvasRef.current?.fitView());
        }}
        onFitCanvas={() => canvasRef.current?.fitView()}
        onHighlightInGraph={() => {
          setViewMode("graph");
          window.requestAnimationFrame(focusCurrentSelection);
        }}
        onHoverNode={setHoveredNodeId}
        onInspectorOpenChange={setInspectorOpen}
        onInspectorTabChange={setInspectorTab}
        onLayerFilterChange={setLayerFilter}
        onRecenter={() => canvasRef.current?.recenter(selectedNodeId)}
        onResetView={handleResetView}
        onSearchQueryChange={setSearchQuery}
        onSearchSubmit={handleSearchSubmit}
        onSelectNode={setSelectedNodeId}
        onStatusFilterChange={setStatusFilter}
        onSummarySelect={handleSummarySelect}
        onToggleLegend={() => setLegendOpen(!legendOpen)}
        onViewModeChange={handleViewModeChange}
        onZoomIn={() => canvasRef.current?.zoomIn()}
        onZoomOut={() => canvasRef.current?.zoomOut()}
        paths={pathsForSelected}
        searchFeedback={searchFeedback}
        searchQuery={searchQuery}
        selectedNode={selectedNode}
        selectedNodeId={selectedNodeId}
        statusFilter={statusFilter}
        summaryFilter={summaryFilter}
        summaryMetrics={summaryMetrics}
        tree={tree}
        upstream={relations.upstream}
        viewMode={viewMode === "impact" ? "graph" : viewMode}
      />
    </div>
  );
}

export default TopologyPage;
