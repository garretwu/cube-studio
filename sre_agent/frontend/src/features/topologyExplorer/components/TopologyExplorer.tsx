import { Select, Spin } from "antd";
import { useMemo, useState, type KeyboardEvent, type Ref } from "react";

import type { TopologyObject, TopologyRelation } from "../../../api/types";
import { AppButton, AppInput, StatusChip } from "../../../components/ui";
import { AppIcon } from "../../../components/ui/AppIcon";
import {
  formatTopologyStatus,
  formatTopologyType,
  getLocationLabel,
  getStatusTone,
} from "../formatters";
import type { TopologyTreeNode } from "../selectors";
import type {
  ExplorerLayerFilter,
  ExplorerLayoutPreset,
  ExplorerViewMode,
  SearchFeedback,
} from "../types";
import TopologyCanvas, {
  type TopologyCanvasHandle,
  type TopologyCanvasNodeAction,
} from "./TopologyCanvas";
import TopologyLegend from "./TopologyLegend";
import TopologyTreeView from "./TopologyTreeView";

type TopologyExplorerProps = {
  isLoading: boolean;
  error?: string;
  hasSourceData: boolean;
  searchQuery: string;
  searchFeedback: SearchFeedback;
  searchResultNodes: TopologyObject[];
  layerFilter: ExplorerLayerFilter;
  viewMode: ExplorerViewMode;
  layoutPreset: ExplorerLayoutPreset;
  legendOpen: boolean;
  filterPanelOpen: boolean;
  graphNodes: TopologyObject[];
  graphEdges: TopologyRelation[];
  tree: TopologyTreeNode | null;
  selectedNode?: TopologyObject;
  selectedNodeId?: string;
  hoveredNodeId?: string;
  matchedNodeIds: string[];
  neighborDepths: Map<string, number>;
  canvasRef: Ref<TopologyCanvasHandle>;
  onSearchQueryChange: (value: string) => void;
  onSearchSubmit: () => void;
  onSearchResultSelect: (nodeId: string) => void;
  onLayerFilterChange: (value: ExplorerLayerFilter) => void;
  onViewModeChange: (value: ExplorerViewMode) => void;
  onResetView: () => void;
  onToggleLegend: () => void;
  onFitCanvas: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onCycleLayoutPreset: () => void;
  onRecenter: () => void;
  onSelectNode: (nodeId: string) => void;
  onHoverNode: (nodeId?: string) => void;
  onFilterPanelOpenChange: (open: boolean) => void;
  onOpenObjectTopology: (nodeId: string, mode: "default" | "isolate") => void;
};

const layerOptions = [
  { value: "all", label: "全部层级" },
  { value: "physical", label: "物理层" },
  { value: "network", label: "网络层" },
  { value: "compute", label: "计算层" },
  { value: "service", label: "服务层" },
] satisfies Array<{ value: ExplorerLayerFilter; label: string }>;

const canvasViews = [
  { key: "graph" as const, label: "关系图" },
  { key: "tree" as const, label: "树视图" },
];

function EmptyState({ children }: { children: string }) {
  return <div className="topology-modified-empty">{children}</div>;
}

function TopologyExplorer({
  isLoading,
  error,
  hasSourceData,
  searchQuery,
  searchFeedback,
  searchResultNodes,
  layerFilter,
  viewMode,
  layoutPreset,
  legendOpen,
  filterPanelOpen,
  graphNodes,
  graphEdges,
  tree,
  selectedNode,
  selectedNodeId,
  hoveredNodeId,
  matchedNodeIds,
  neighborDepths,
  canvasRef,
  onSearchQueryChange,
  onSearchSubmit,
  onSearchResultSelect,
  onLayerFilterChange,
  onViewModeChange,
  onResetView,
  onToggleLegend,
  onFitCanvas,
  onZoomIn,
  onZoomOut,
  onCycleLayoutPreset,
  onRecenter,
  onSelectNode,
  onHoverNode,
  onFilterPanelOpenChange,
  onOpenObjectTopology,
}: TopologyExplorerProps) {
  const [zoomPercent, setZoomPercent] = useState(100);
  const [nodeActions, setNodeActions] = useState<TopologyCanvasNodeAction | null>(null);
  const isTreeView = viewMode === "tree";
  const isGraphEmpty = !isTreeView && graphNodes.length === 0;
  const isTreeEmpty = isTreeView && !tree;
  const canRenderOverlay = !isLoading && !error && hasSourceData;

  const searchStatus =
    searchFeedback === "not_found"
      ? { tone: "warning" as const, label: "未找到匹配对象" }
      : searchQuery.trim() && searchResultNodes.length > 0
        ? { tone: "accent" as const, label: `命中 ${searchResultNodes.length}` }
        : null;

  const actionNode = nodeActions?.node;
  const actionPosition = useMemo(
    () =>
      nodeActions
        ? {
            left: Math.min(nodeActions.clientX + 16, window.innerWidth - 360),
            top: Math.max(nodeActions.clientY - 24, 92),
          }
        : null,
    [nodeActions],
  );

  const handleKeyDown = (
    event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    if (event.key === "Enter") {
      event.preventDefault();
      onSearchSubmit();
    }
  };

  const handleResultSelect = (nodeId: string) => {
    setNodeActions(null);
    onSearchResultSelect(nodeId);
  };

  const handleCanvasSelect = (nodeId: string) => {
    onSelectNode(nodeId);
  };

  return (
    <div className="topology-stage-workplane" data-testid="topology-stage-workplane">
      <div className="topology-stage-shell" data-testid="topology-explorer-stage">
        {canRenderOverlay ? (
          <>
            <div className="topology-stage-dock topology-stage-dock--secondary">
              {canvasViews.map((view) => (
                <button
                  key={view.key}
                  className={`topology-stage-dock__button ${viewMode === view.key ? "topology-stage-dock__button--active" : ""}`}
                  onClick={() => {
                    setNodeActions(null);
                    onViewModeChange(view.key);
                  }}
                  type="button"
                >
                  {view.label}
                </button>
              ))}
              <button
                className={`topology-stage-dock__button ${legendOpen ? "topology-stage-dock__button--active" : ""}`}
                onClick={onToggleLegend}
                type="button"
              >
                图例
              </button>
            </div>

            <div className="topology-stage-dock topology-stage-dock--primary" data-testid="topology-filter-dock">
              <button
                className={`topology-stage-dock__button ${filterPanelOpen ? "topology-stage-dock__button--active" : ""}`}
                onClick={() => {
                  setNodeActions(null);
                  onFilterPanelOpenChange(!filterPanelOpen);
                }}
                type="button"
              >
                筛选
              </button>
              <button className="topology-stage-dock__button" onClick={onFitCanvas} type="button">
                适配
              </button>
              <button className="topology-stage-dock__button" onClick={onResetView} type="button">
                重置
              </button>
            </div>

            {filterPanelOpen ? (
              <section className="topology-stage-filter-panel" data-testid="topology-filter-panel">
                <div className="topology-stage-filter-panel__header">
                  <div>
                    <p className="topology-stage-filter-panel__eyebrow">拓扑筛选</p>
                    <h3 className="topology-stage-filter-panel__title">搜索与收敛视图</h3>
                  </div>
                  <button
                    className="topology-stage-filter-panel__close"
                    onClick={() => onFilterPanelOpenChange(false)}
                    type="button"
                    aria-label="关闭筛选"
                  >
                    ×
                  </button>
                </div>

                <div className="topology-stage-filter-panel__controls">
                  <AppInput
                    className="topology-stage-filter-panel__search"
                    onChange={onSearchQueryChange}
                    onKeyDown={handleKeyDown}
                    placeholder="搜索机柜 / 节点 / GPU / 服务 / 交换机"
                    prefix={<AppIcon name="search" size={16} />}
                    value={searchQuery}
                  />
                  <Select
                    className="app-select topology-stage-filter-panel__select"
                    onChange={(value) => onLayerFilterChange(value)}
                    options={layerOptions}
                    value={layerFilter}
                  />
                  <div className="topology-stage-filter-panel__action-row">
                    <AppButton size="sm" variant="secondary" onClick={onCycleLayoutPreset}>
                      {layoutPreset === "layered" ? "切换域布局" : "切换层级布局"}
                    </AppButton>
                    {searchQuery.trim() ? (
                      <AppButton size="sm" variant="secondary" onClick={onSearchSubmit}>
                        聚焦首个结果
                      </AppButton>
                    ) : null}
                  </div>
                </div>

                {searchStatus ? (
                  <div className="topology-stage-filter-panel__status">
                    <StatusChip tone={searchStatus.tone}>{searchStatus.label}</StatusChip>
                  </div>
                ) : null}

                <div className="topology-stage-filter-panel__results">
                  <div className="topology-stage-filter-panel__results-header">
                    <span>结果列表</span>
                    <span>{searchResultNodes.length}</span>
                  </div>
                  {searchQuery.trim() ? (
                    searchResultNodes.length > 0 ? (
                      <div className="topology-stage-result-list">
                        {searchResultNodes.map((node) => (
                          <button
                            key={node.id}
                            className={`topology-stage-result ${selectedNodeId === node.id ? "topology-stage-result--active" : ""}`}
                            onClick={() => handleResultSelect(node.id)}
                            type="button"
                          >
                            <span className="topology-stage-result__title">{node.name}</span>
                            <span className="topology-stage-result__meta">
                              {formatTopologyType(node.type)} · {formatTopologyStatus(node.status)}
                            </span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="topology-stage-filter-panel__empty">当前筛选条件下没有匹配对象。</p>
                    )
                  ) : (
                    <p className="topology-stage-filter-panel__empty">输入对象名称后，画布会收敛到命中对象及其直接关联对象。</p>
                  )}
                </div>
              </section>
            ) : null}

            <div className="topology-stage-dock topology-stage-dock--zoom">
              <span className="topology-stage-dock__zoom-label">{zoomPercent}%</span>
              <button className="topology-stage-dock__button topology-stage-dock__button--icon" onClick={onZoomOut} type="button">
                -
              </button>
              <button className="topology-stage-dock__button topology-stage-dock__button--icon" onClick={onZoomIn} type="button">
                +
              </button>
              <button className="topology-stage-dock__button" onClick={onRecenter} type="button">
                居中
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
          <EmptyState>当前没有拓扑数据，请稍后重试。</EmptyState>
        ) : isGraphEmpty ? (
          <EmptyState>当前筛选条件下没有可展示对象，请调整筛选或重置视图。</EmptyState>
        ) : isTreeEmpty ? (
          <EmptyState>当前没有可展示的树视图数据，请切回关系图查看。</EmptyState>
        ) : isTreeView ? (
          <TopologyTreeView
            onSelectNode={handleResultSelect}
            selectedNodeId={selectedNodeId}
            tree={tree}
          />
        ) : (
          <TopologyCanvas
            ref={canvasRef}
            edges={graphEdges}
            forceEdgeLabels={false}
            hoveredNodeId={hoveredNodeId}
            layoutPreset={layoutPreset}
            matchedNodeIds={matchedNodeIds}
            neighborDepths={neighborDepths}
            nodes={graphNodes}
            onCanvasInteraction={() => setNodeActions(null)}
            onHoverNode={onHoverNode}
            onOpenNodeActions={(payload) => {
              onFilterPanelOpenChange(false);
              setNodeActions(payload);
            }}
            onSelectNode={handleCanvasSelect}
            onZoomChange={setZoomPercent}
            selectedNodeId={selectedNodeId}
          />
        )}
      </div>

      {actionNode && actionPosition ? (
        <aside
          className="topology-node-menu"
          data-testid="topology-node-popover"
          style={actionPosition}
        >
          <div className="topology-node-menu__header">
            <div>
              <p className="topology-node-menu__eyebrow">{formatTopologyType(actionNode.type)}</p>
              <h3 className="topology-node-menu__title">{actionNode.name}</h3>
            </div>
            <StatusChip tone={getStatusTone(actionNode.status)}>{formatTopologyStatus(actionNode.status)}</StatusChip>
          </div>
          <dl className="topology-node-menu__facts">
            <div>
              <dt>位置</dt>
              <dd>{getLocationLabel(actionNode) || "未提供"}</dd>
            </div>
            <div>
              <dt>摘要</dt>
              <dd>{actionNode.summary}</dd>
            </div>
          </dl>
          <div className="topology-node-menu__actions">
            <AppButton
              size="sm"
              variant="secondary"
              onClick={() => onOpenObjectTopology(actionNode.id, "isolate")}
            >
              Isolate
            </AppButton>
            <AppButton
              size="sm"
              variant="primary"
              onClick={() => onOpenObjectTopology(actionNode.id, "default")}
            >
              View topology
            </AppButton>
          </div>
          {selectedNode && selectedNode.id === actionNode.id ? (
            <p className="topology-node-menu__hint">当前对象已被选中，右键外空白区域可关闭此浮层。</p>
          ) : null}
        </aside>
      ) : null}
    </div>
  );
}

export default TopologyExplorer;
