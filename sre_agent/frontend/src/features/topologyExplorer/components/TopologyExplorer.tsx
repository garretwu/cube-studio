import { Select, Spin } from "antd";
import { useState, type KeyboardEvent, type Ref } from "react";

import type { TopologyObject, TopologyPath, TopologyRelation } from "../../../api/types";
import { AppButton, AppInput, StatusChip, SurfaceCard } from "../../../components/ui";
import { AppIcon } from "../../../components/ui/AppIcon";
import type { TopologySummaryMetrics, TopologyTreeNode } from "../selectors";
import type {
  ExplorerLayerFilter,
  ExplorerLayoutPreset,
  ExplorerStatusFilter,
  ExplorerSummaryFilter,
  ExplorerViewMode,
  InspectorTabKey,
  SearchFeedback,
} from "../types";
import ObjectInspector from "./ObjectInspector";
import TopologyCanvas, { type TopologyCanvasHandle } from "./TopologyCanvas";
import TopologyLegend from "./TopologyLegend";
import TopologyTreeView from "./TopologyTreeView";

type TopologyExplorerProps = {
  isLoading: boolean;
  error?: string;
  hasSourceData: boolean;
  lastUpdated?: string;
  searchQuery: string;
  searchFeedback: SearchFeedback;
  matchedCount: number;
  statusFilter: ExplorerStatusFilter;
  layerFilter: ExplorerLayerFilter;
  summaryFilter: ExplorerSummaryFilter;
  viewMode: ExplorerViewMode;
  layoutPreset: ExplorerLayoutPreset;
  canExport: boolean;
  exportHint?: string;
  legendOpen: boolean;
  inspectorOpen: boolean;
  summaryMetrics: TopologySummaryMetrics;
  graphNodes: TopologyObject[];
  graphEdges: TopologyRelation[];
  tree: TopologyTreeNode | null;
  selectedNode?: TopologyObject;
  selectedNodeId?: string;
  hoveredNodeId?: string;
  matchedNodeIds: string[];
  neighborDepths: Map<string, number>;
  inspectorTab: InspectorTabKey;
  upstream: TopologyObject[];
  downstream: TopologyObject[];
  neighbors: TopologyObject[];
  paths: TopologyPath[];
  affectedObjects: TopologyObject[];
  canvasRef: Ref<TopologyCanvasHandle>;
  onSearchQueryChange: (value: string) => void;
  onSearchSubmit: () => void;
  onStatusFilterChange: (value: ExplorerStatusFilter) => void;
  onLayerFilterChange: (value: ExplorerLayerFilter) => void;
  onSummarySelect: (value: ExplorerSummaryFilter) => void;
  onViewModeChange: (value: ExplorerViewMode) => void;
  onResetView: () => void;
  onToggleLegend: () => void;
  onFitCanvas: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onCycleLayoutPreset: () => void;
  onExport: () => void;
  onRecenter: () => void;
  onSelectNode: (nodeId: string) => void;
  onHoverNode: (nodeId?: string) => void;
  onCanvasReadyStateChange: (ready: boolean) => void;
  onInspectorOpenChange: (open: boolean) => void;
  onInspectorTabChange: (key: InspectorTabKey) => void;
  onHighlightInGraph: () => void;
};

const layerOptions = [
  { value: "all", label: "全部层级" },
  { value: "physical", label: "物理层" },
  { value: "network", label: "网络层" },
  { value: "compute", label: "计算层" },
  { value: "service", label: "服务层" },
] satisfies Array<{ value: ExplorerLayerFilter; label: string }>;

const summaryPills = [
  { key: "all" as const, label: "总实体", tone: "neutral", getValue: (metrics: TopologySummaryMetrics) => metrics.totalEntities },
  { key: "abnormal" as const, label: "异常", tone: "danger", getValue: (metrics: TopologySummaryMetrics) => metrics.abnormalEntities },
  { key: "impacted" as const, label: "受影响", tone: "warning", getValue: (metrics: TopologySummaryMetrics) => metrics.impactedEntities },
] satisfies Array<{
  key: ExplorerSummaryFilter;
  label: string;
  tone: "neutral" | "danger" | "warning" | "info";
  getValue: (metrics: TopologySummaryMetrics) => number;
}>;

const canvasViews = [
  { key: "graph" as const, label: "关系图" },
  { key: "tree" as const, label: "属性图" },
];

function EmptyState({ children }: { children: string }) {
  return <div className="topology-modified-empty">{children}</div>;
}

function TopologyExplorer({
  isLoading,
  error,
  hasSourceData,
  lastUpdated,
  searchQuery,
  searchFeedback,
  matchedCount,
  statusFilter,
  layerFilter,
  summaryFilter,
  viewMode,
  layoutPreset,
  canExport,
  exportHint,
  legendOpen,
  inspectorOpen,
  summaryMetrics,
  graphNodes,
  graphEdges,
  tree,
  selectedNode,
  selectedNodeId,
  hoveredNodeId,
  matchedNodeIds,
  neighborDepths,
  inspectorTab,
  upstream,
  downstream,
  neighbors,
  paths,
  affectedObjects,
  canvasRef,
  onSearchQueryChange,
  onSearchSubmit,
  onStatusFilterChange,
  onLayerFilterChange,
  onSummarySelect,
  onViewModeChange,
  onResetView,
  onToggleLegend,
  onFitCanvas,
  onZoomIn,
  onZoomOut,
  onCycleLayoutPreset,
  onExport,
  onRecenter,
  onSelectNode,
  onHoverNode,
  onCanvasReadyStateChange,
  onInspectorOpenChange,
  onInspectorTabChange,
  onHighlightInGraph,
}: TopologyExplorerProps) {
  const [zoomPercent, setZoomPercent] = useState(100);
  const isTreeView = viewMode === "tree";
  const isGraphEmpty = !isTreeView && graphNodes.length === 0;
  const isTreeEmpty = isTreeView && !tree;
  const canRenderOverlay = !isLoading && !error && hasSourceData;

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      onSearchSubmit();
    }
  };

  const searchStatus =
    searchFeedback === "not_found"
      ? { tone: "warning" as const, label: "未找到结果" }
      : searchQuery.trim() && matchedCount > 0
        ? { tone: "accent" as const, label: `命中 ${matchedCount}` }
        : null;

  return (
    <SurfaceCard className="topology-modified-explorer">
      <div className="topology-modified-explorer__shell">
        <div className="topology-modified-explorer__summary-strip" role="group" aria-label="拓扑摘要筛选">
          {summaryPills.map((pill) => (
            <button
              key={pill.key}
              className={`topology-modified-explorer__summary-pill topology-modified-explorer__summary-pill--${pill.tone} ${summaryFilter === pill.key ? "topology-modified-explorer__summary-pill--active" : ""}`}
              onClick={() => onSummarySelect(pill.key)}
              type="button"
            >
              <span className="topology-modified-explorer__summary-label">{pill.label}</span>
              <strong className="topology-modified-explorer__summary-value">{pill.getValue(summaryMetrics)}</strong>
            </button>
          ))}
        </div>

        <div className="topology-modified-explorer__controls" data-testid="topology-explorer-toolbar">
          <div className="topology-modified-explorer__search-row">
            <div className="topology-modified-explorer__search-field">
              <AppInput
                className="topology-modified-explorer__search-input"
                onChange={onSearchQueryChange}
                onKeyDown={handleKeyDown}
                placeholder="搜索机柜 / 节点 / GPU / 服务 / 交换机"
                prefix={<AppIcon name="search" size={16} />}
                value={searchQuery}
              />
              <AppButton size="sm" variant="primary" onClick={onSearchSubmit}>
                定位对象
              </AppButton>
              {searchStatus ? <StatusChip tone={searchStatus.tone}>{searchStatus.label}</StatusChip> : null}
            </div>
          </div>

          <div className="topology-modified-explorer__segmented topology-modified-explorer__segmented--status" role="group" aria-label="状态筛选">
            <button
              className={`topology-modified-toggle ${statusFilter === "all" ? "topology-modified-toggle--active" : ""}`}
              onClick={() => onStatusFilterChange("all")}
              type="button"
            >
              全部对象
            </button>
            <button
              className={`topology-modified-toggle ${statusFilter === "abnormal" ? "topology-modified-toggle--active" : ""}`}
              onClick={() => onStatusFilterChange(statusFilter === "abnormal" ? "all" : "abnormal")}
              type="button"
            >
              仅异常
            </button>
          </div>

          <Select
            className="app-select topology-modified-explorer__select"
            onChange={(value) => onLayerFilterChange(value)}
            options={layerOptions}
            value={layerFilter}
          />

          <div className="topology-modified-explorer__control-actions">
            <AppButton disabled={!canExport} iconLeft="documentZip" size="sm" title={exportHint} variant="secondary" onClick={onExport}>
              导出拓扑
            </AppButton>
            <AppButton size="sm" variant="secondary" onClick={onCycleLayoutPreset}>
              {layoutPreset === "layered" ? "切换域布局" : "切换层级布局"}
            </AppButton>
            <AppButton size="sm" variant="secondary" onClick={onFitCanvas}>
              适配
            </AppButton>
            <AppButton size="sm" variant="secondary" onClick={onResetView}>
              重置
            </AppButton>
          </div>
        </div>

        {!canExport && exportHint ? <p className="topology-modified-explorer__export-hint">{exportHint}</p> : null}

        <div className="topology-modified-explorer__stage" data-testid="topology-explorer-stage">
          <div className={`topology-modified-explorer__stage-shell ${inspectorOpen ? "topology-modified-explorer__stage-shell--panel-open" : ""}`}>
            <div className="topology-modified-explorer__canvas-region">
              {canRenderOverlay ? (
                <>
                  <div className="topology-modified-canvas-dock topology-modified-canvas-dock--views">
                    {canvasViews.map((view) => (
                      <button
                        key={view.key}
                        className={`topology-modified-canvas-dock__button ${viewMode === view.key ? "topology-modified-canvas-dock__button--active" : ""}`}
                        onClick={() => onViewModeChange(view.key)}
                        type="button"
                      >
                        {view.label}
                      </button>
                    ))}
                    <button
                      className={`topology-modified-canvas-dock__button ${legendOpen ? "topology-modified-canvas-dock__button--active" : ""}`}
                      onClick={onToggleLegend}
                      type="button"
                    >
                      图例
                    </button>
                  </div>

                  <div className="topology-modified-canvas-dock topology-modified-canvas-dock--panel">
                    <button className="topology-modified-canvas-dock__button" onClick={() => onInspectorOpenChange(!inspectorOpen)} type="button">
                      {inspectorOpen ? "隐藏检查器" : "显示检查器"}
                    </button>
                  </div>

                  <div className="topology-modified-canvas-dock topology-modified-canvas-dock--zoom">
                    <span className="topology-modified-canvas-dock__zoom-label">{zoomPercent}%</span>
                    <button className="topology-modified-canvas-dock__button topology-modified-canvas-dock__button--icon" onClick={onZoomOut} type="button">
                      -
                    </button>
                    <button className="topology-modified-canvas-dock__button topology-modified-canvas-dock__button--icon" onClick={onZoomIn} type="button">
                      +
                    </button>
                    <button className="topology-modified-canvas-dock__button" onClick={onRecenter} type="button">
                      重置
                    </button>
                  </div>

                  {!isTreeView ? <TopologyLegend open={legendOpen} /> : null}
                </>
              ) : null}

              {isLoading ? (
                <div className="state-block">
                  <Spin />
                </div>
              ) : error ? (
                <EmptyState>{error}</EmptyState>
              ) : !hasSourceData ? (
                <EmptyState>当前无拓扑数据，请重新加载后再试。</EmptyState>
              ) : isGraphEmpty ? (
                <EmptyState>当前筛选条件下没有可展示对象，建议重置视图或切换层级。</EmptyState>
              ) : isTreeEmpty ? (
                <EmptyState>当前无可渲染属性图，请返回关系图检查数据。</EmptyState>
              ) : isTreeView ? (
                <TopologyTreeView onSelectNode={onSelectNode} selectedNodeId={selectedNodeId} tree={tree} />
              ) : (
                <TopologyCanvas
                  ref={canvasRef}
                  edges={graphEdges}
                  forceEdgeLabels={false}
                  hoveredNodeId={hoveredNodeId}
                  lastUpdated={lastUpdated}
                  layerFilter={layerFilter}
                  layoutPreset={layoutPreset}
                  matchedNodeIds={matchedNodeIds}
                  neighborDepths={neighborDepths}
                  nodes={graphNodes}
                  onHoverNode={onHoverNode}
                  onReadyStateChange={onCanvasReadyStateChange}
                  onSelectNode={onSelectNode}
                  onZoomChange={setZoomPercent}
                  searchQuery={searchQuery}
                  selectedNodeId={selectedNodeId}
                  statusFilter={statusFilter}
                  summaryFilter={summaryFilter}
                />
              )}
            </div>

            <ObjectInspector
              affectedObjects={affectedObjects}
              downstream={downstream}
              inspectorTab={inspectorTab}
              neighbors={neighbors}
              node={selectedNode}
              onHighlightInGraph={onHighlightInGraph}
              onOpenChange={onInspectorOpenChange}
              onSelectNode={onSelectNode}
              onTabChange={onInspectorTabChange}
              open={inspectorOpen}
              paths={paths}
              upstream={upstream}
            />
          </div>
        </div>
      </div>
    </SurfaceCard>
  );
}

export default TopologyExplorer;
