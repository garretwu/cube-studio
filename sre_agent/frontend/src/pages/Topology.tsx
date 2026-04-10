import { useEffect, useMemo, useRef, useState } from "react";

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

function formatExportFileTimestamp(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  const hours = String(value.getHours()).padStart(2, "0");
  const minutes = String(value.getMinutes()).padStart(2, "0");
  const seconds = String(value.getSeconds()).padStart(2, "0");

  return `${year}${month}${day}-${hours}${minutes}${seconds}`;
}

function downloadTopologyExport(payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = `topology-canvas-${formatExportFileTimestamp()}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  window.URL.revokeObjectURL(objectUrl);
}

function TopologyPage() {
  const canvasRef = useRef<TopologyCanvasHandle | null>(null);
  const [canvasReady, setCanvasReady] = useState(false);
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
  const resolvedViewMode = viewMode === "impact" ? "graph" : viewMode;
  const canExportTopology =
    resolvedViewMode === "graph" && canvasReady && visibleTopology.nodes.length > 0 && Boolean(canvasRef.current);
  const exportHint =
    resolvedViewMode !== "graph"
      ? "仅关系图视图支持导出当前画布"
      : !visibleTopology.nodes.length
        ? "当前画布没有可导出的拓扑对象"
        : !canvasReady
          ? "拓扑画布尚未完成初始化"
          : undefined;

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

  const handleExport = () => {
    const exportedView = canvasRef.current?.exportView();
    if (!exportedView) {
      return;
    }

    downloadTopologyExport(exportedView);
  };

  return (
    <div className="page-grid topology-modified-page">
      <TopologyHeader lastUpdated={data?.lastUpdated} />

      <TopologyExplorer
        affectedObjects={affectedObjects}
        canExport={canExportTopology}
        canvasRef={canvasRef}
        downstream={relations.downstream}
        error={error}
        exportHint={exportHint}
        graphEdges={visibleTopology.edges}
        graphNodes={visibleTopology.nodes}
        hasSourceData={Boolean(data?.nodes?.length)}
        hoveredNodeId={hoveredNodeId}
        inspectorOpen={inspectorOpen}
        inspectorTab={inspectorTab}
        isLoading={isLoading}
        layerFilter={layerFilter}
        layoutPreset={layoutPreset}
        lastUpdated={data?.lastUpdated}
        legendOpen={legendOpen}
        matchedCount={matchedNodeIds.length}
        matchedNodeIds={matchedNodeIds}
        neighborDepths={neighborDepths}
        neighbors={relations.neighbors}
        onCanvasReadyStateChange={setCanvasReady}
        onCycleLayoutPreset={() => {
          cycleLayoutPreset();
          window.requestAnimationFrame(() => canvasRef.current?.fitView());
        }}
        onExport={handleExport}
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
        viewMode={resolvedViewMode}
      />
    </div>
  );
}

export default TopologyPage;
