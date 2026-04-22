import { Select, Spin, Tooltip } from "antd";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type Ref,
} from "react";
import { createPortal } from "react-dom";

import type { TopologyObject, TopologyRelation } from "../../../api/types";
import { AppButton, AppInput, StatusChip } from "../../../components/ui";
import { AppIcon } from "../../../components/ui/AppIcon";
import {
  formatTopologyStatus,
  formatTopologyType,
  getLocationLabel,
  getStatusTone,
  getTopologyTypeIconAsset,
} from "../formatters";
import { isSyntheticBmcAggregateNode, isSyntheticGpuAggregateNode, isSyntheticServiceAggregateNode, type TopologyTreeNode } from "../selectors";
import {
  topologyCanvasViewControls,
  topologyLegendTypeOrder,
  topologyRoomOptions,
} from "../uiConfig";
import type {
  ExplorerLayerFilter,
  ExplorerLayoutPreset,
  ExplorerViewMode,
  SearchFeedback,
  TopologyScopeMode,
} from "../types";
import TopologyCanvas, {
  type TopologyCanvasHandle,
  type TopologyCanvasNodeAction,
} from "./TopologyCanvas";
import TopologyTreeView from "./TopologyTreeView";

type TopologyExplorerProps = {
  isLoading: boolean;
  error?: string;
  hasSourceData: boolean;
  syncState?: "idle" | "syncing" | "ready" | "degraded" | "error";
  lastError?: string | null;
  allNodes: TopologyObject[];
  scopeMode: TopologyScopeMode;
  selectedRoomId: string;
  searchQuery: string;
  searchFeedback: SearchFeedback;
  searchResultNodes: TopologyObject[];
  layerFilter: ExplorerLayerFilter;
  viewMode: ExplorerViewMode;
  layoutPreset: ExplorerLayoutPreset;
  secondaryPanelOpen: boolean;
  filterPanelOpen: boolean;
  variant?: "default" | "modified";
  graphNodes: TopologyObject[];
  graphEdges: TopologyRelation[];
  tree: TopologyTreeNode | null;
  selectedNode?: TopologyObject;
  selectedNodeId?: string;
  hoveredNodeId?: string;
  matchedNodeIds: string[];
  neighborDepths: Map<string, number>;
  canvasRef: Ref<TopologyCanvasHandle>;
  onScopeModeChange: (value: TopologyScopeMode) => void;
  onSelectedRoomChange: (value: string) => void;
  onSearchQueryChange: (value: string) => void;
  onSearchSubmit: () => void;
  onSearchResultSelect: (nodeId: string) => void;
  onLayerFilterChange: (value: ExplorerLayerFilter) => void;
  onViewModeChange: (value: ExplorerViewMode) => void;
  onLayoutPresetChange: (value: ExplorerLayoutPreset) => void;
  onResetView: () => void;
  onFitCanvas: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onRecenter: () => void;
  onSelectNode: (nodeId: string) => void;
  onHoverNode: (nodeId?: string) => void;
  onSecondaryPanelOpenChange: (open: boolean) => void;
  onFilterPanelOpenChange: (open: boolean) => void;
  onOpenObjectTopology: (nodeId: string, mode: "default" | "isolate") => void;
  onToggleAggregateNode?: (aggregateId: string) => void;
  expandedAggregateIds?: string[];
  expandedAggregateMeta?: Record<string, { aggregateId: string; label: string; memberIds: string[] }>;
  onCollapseAggregate?: (aggregateId: string) => void;
};

const layerOptions = [
  { value: "all", label: "\u5168\u90e8\u5c42\u7ea7" },
  { value: "physical", label: "\u7269\u7406\u5c42" },
  { value: "network", label: "\u7f51\u7edc\u5c42" },
  { value: "compute", label: "\u8ba1\u7b97\u5c42" },
  { value: "service", label: "\u670d\u52a1\u5c42" },
] satisfies Array<{ value: ExplorerLayerFilter; label: string }>;

type FilterPanelEntryMode = "search" | "filter";

function EmptyState({ children }: { children: string }) {
  return <div className="topology-modified-empty">{children}</div>;
}

function IconToolbarButton({
  active = false,
  disabled = false,
  icon,
  label,
  onClick,
}: {
  active?: boolean;
  disabled?: boolean;
  icon: Parameters<typeof AppIcon>[0]["name"];
  label: string;
  onClick: () => void;
}) {
  return (
    <Tooltip placement="left" title={label}>
      <button
        aria-label={label}
        className={`topology-stage-icon-button ${active ? "topology-stage-icon-button--active" : ""}`}
        disabled={disabled}
        onClick={onClick}
        title={label}
        type="button"
      >
        <AppIcon name={icon} size={16} />
      </button>
    </Tooltip>
  );
}

function TopologyExplorer({
  isLoading,
  error,
  hasSourceData,
  syncState,
  lastError,
  allNodes,
  scopeMode,
  selectedRoomId,
  searchQuery,
  searchFeedback,
  searchResultNodes,
  layerFilter,
  viewMode,
  layoutPreset,
  secondaryPanelOpen,
  filterPanelOpen,
  variant = "default",
  graphNodes,
  graphEdges,
  tree,
  selectedNode,
  selectedNodeId,
  hoveredNodeId,
  matchedNodeIds,
  neighborDepths,
  canvasRef,
  onScopeModeChange,
  onSelectedRoomChange,
  onSearchQueryChange,
  onSearchSubmit,
  onSearchResultSelect,
  onLayerFilterChange,
  onViewModeChange,
  onLayoutPresetChange,
  onResetView,
  onFitCanvas,
  onZoomIn,
  onZoomOut,
  onRecenter,
  onSelectNode,
  onHoverNode,
  onSecondaryPanelOpenChange,
  onFilterPanelOpenChange,
  onOpenObjectTopology,
  onToggleAggregateNode,
  expandedAggregateIds,
  expandedAggregateMeta,
  onCollapseAggregate,
}: TopologyExplorerProps) {
  const [zoomPercent, setZoomPercent] = useState(100);
  const [nodeActions, setNodeActions] = useState<TopologyCanvasNodeAction | null>(null);
  const [filterPanelEntryMode, setFilterPanelEntryMode] = useState<FilterPanelEntryMode>("filter");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [topbarInlineSlot, setTopbarInlineSlot] = useState<HTMLElement | null>(null);
  const [highlightedLegendType, setHighlightedLegendType] = useState<TopologyObject["type"] | null>(null);
  const searchInputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const stageShellRef = useRef<HTMLDivElement | null>(null);

  const isTreeView = viewMode === "tree";
  const isGraphEmpty = !isTreeView && graphNodes.length === 0;
  const isTreeEmpty = isTreeView && !tree;
  const canRenderOverlay = !isLoading && !error && hasSourceData;
  const fullscreenSupported =
    typeof document !== "undefined" && typeof document.documentElement.requestFullscreen === "function";
  const canvasViewInsets = useMemo(
    () =>
      variant === "modified"
        ? {
            left: secondaryPanelOpen ? 328 : 72,
            right: filterPanelOpen ? 452 : 88,
            top: 24,
            bottom: 24,
          }
        : undefined,
    [filterPanelOpen, secondaryPanelOpen, variant],
  );

  const legendItems = useMemo(() => {
    const counts = allNodes.reduce<Record<string, number>>((accumulator, node) => {
      if (node.type === "service") {
        // "服务组" should represent namespace groups only (ns:*),
        // not every Kubernetes Service object (svc:*).
        const kind = String(node.attributes.kind ?? "").toLowerCase();
        if (kind !== "namespace_group") {
          return accumulator;
        }
      }
      accumulator[node.type] = (accumulator[node.type] ?? 0) + 1;
      return accumulator;
    }, {});

    return topologyLegendTypeOrder
      .map((type) => ({
        type,
        label: formatTopologyType(type),
        count: counts[type] ?? 0,
        iconSrc: getTopologyTypeIconAsset(type),
      }))
      .filter((item) => item.count > 0);
  }, [allNodes]);

  const selectedRoom =
    topologyRoomOptions.find((option) => option.id === selectedRoomId) ?? topologyRoomOptions[0];

  const activeViewControlId = viewMode === "tree" ? "tree" : "graph-layered";

  const searchStatus =
    searchFeedback === "not_found"
      ? { tone: "warning" as const, label: "\u672a\u627e\u5230\u5339\u914d\u5bf9\u8c61" }
      : searchQuery.trim() && searchResultNodes.length > 0
        ? { tone: "accent" as const, label: `\u547d\u4e2d ${searchResultNodes.length}` }
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

  useEffect(() => {
    if (typeof document === "undefined") {
      return undefined;
    }

    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === stageShellRef.current);
    };

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    handleFullscreenChange();

    return () => {
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
    };
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }

    setTopbarInlineSlot(document.getElementById("topbar-inline-slot"));
  }, []);

  useEffect(() => {
    if (!nodeActions) {
      return;
    }

    if (!graphNodes.some((node) => node.id === nodeActions.node.id)) {
      setNodeActions(null);
    }
  }, [graphNodes, nodeActions]);

  useEffect(() => {
    if (!nodeActions || nodeActions.trigger !== "hover") {
      return;
    }

    if (!hoveredNodeId || hoveredNodeId !== nodeActions.node.id) {
      setNodeActions(null);
    }
  }, [hoveredNodeId, nodeActions]);

  useEffect(() => {
    if (!filterPanelOpen || filterPanelEntryMode !== "search") {
      return;
    }

    window.requestAnimationFrame(() => {
      const input = searchInputRef.current;
      if (!input) {
        return;
      }

      input.focus();
      if (typeof input.select === "function") {
        input.select();
      }
    });
  }, [filterPanelEntryMode, filterPanelOpen]);

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
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

  const handleCanvasViewChange = (
    nextViewMode: "graph" | "tree",
    nextLayoutPreset?: ExplorerLayoutPreset,
  ) => {
    setNodeActions(null);
    onViewModeChange(nextViewMode);
    if (nextLayoutPreset) {
      onLayoutPresetChange(nextLayoutPreset);
    }
  };

  const handleLegendTypeToggle = (type: TopologyObject["type"]) => {
    setHighlightedLegendType((current) => (current === type ? null : type));
  };

  const openFilterPanel = (entryMode: FilterPanelEntryMode) => {
    setNodeActions(null);
    setFilterPanelEntryMode(entryMode);
    onFilterPanelOpenChange(true);
  };

  const toggleFilterPanel = (entryMode: FilterPanelEntryMode) => {
    setNodeActions(null);
    if (filterPanelOpen && filterPanelEntryMode === entryMode) {
      onFilterPanelOpenChange(false);
      return;
    }

    setFilterPanelEntryMode(entryMode);
    onFilterPanelOpenChange(true);
  };

  const handleToggleFullscreen = () => {
    const stageShell = stageShellRef.current;
    if (!stageShell || !fullscreenSupported) {
      return;
    }

    if (document.fullscreenElement === stageShell) {
      void document.exitFullscreen?.();
      return;
    }

    void stageShell.requestFullscreen();
  };

  const scopeControls = canRenderOverlay ? (
    <div
      className={`topology-stage-topbar ${topbarInlineSlot ? "topology-stage-topbar--in-global-bar" : ""}`}
      data-testid="topology-stage-topbar"
    >
      <button
        className="topology-stage-topbar__room-trigger"
        data-testid="topology-room-selector"
        onClick={() => onSelectedRoomChange(selectedRoom.id)}
        type="button"
      >
        <span className="topology-stage-topbar__room-leading" aria-hidden="true">
          <AppIcon name={selectedRoom.iconName} size={16} />
        </span>
        <span className="topology-stage-topbar__room-copy">
          <span className="topology-stage-topbar__room-label">{"\u673a\u623f"}</span>
          <span className="topology-stage-topbar__room-name">{selectedRoom.name}</span>
        </span>
      </button>
    </div>
  ) : null;

  return (
    <div
      className={`topology-stage-workplane topology-stage-workplane--${variant}`}
      data-layout-preset={layoutPreset}
      data-testid="topology-stage-workplane"
      data-topology-variant={variant}
      data-view-mode={viewMode}
    >
      {topbarInlineSlot && scopeControls ? createPortal(scopeControls, topbarInlineSlot) : scopeControls}

      {syncState === "degraded" || syncState === "error" ? (
        <div className="topology-sync-warning" data-testid="topology-sync-warning">
          <span className="topology-sync-warning__icon">⚠</span>
          <span className="topology-sync-warning__text">
            {syncState === "error" ? "拓扑同步失败" : "拓扑部分加载失败"}
            {lastError ? `：${lastError}` : ""}
          </span>
        </div>
      ) : null}

      <div className="topology-stage-shell" data-testid="topology-explorer-stage" ref={stageShellRef}>
        {canRenderOverlay ? (
          <>
            {secondaryPanelOpen ? (
              <aside
                className="topology-stage-dock topology-stage-dock--secondary"
                data-testid="topology-secondary-panel"
              >
                <div className="topology-stage-secondary-panel__header">
                  <div>
                    <p className="topology-stage-secondary-panel__eyebrow">{"\u8f85\u52a9\u9762\u677f"}</p>
                    <h3 className="topology-stage-secondary-panel__title">{"\u56fe\u4f8b"}</h3>
                  </div>
                  <Tooltip placement="right" title={"\u6536\u8d77\u56fe\u4f8b\u9762\u677f"}>
                    <button
                      aria-label={"\u6536\u8d77\u56fe\u4f8b\u9762\u677f"}
                      className="topology-stage-secondary-panel__toggle"
                      onClick={() => onSecondaryPanelOpenChange(false)}
                      title={"\u6536\u8d77\u56fe\u4f8b\u9762\u677f"}
                      type="button"
                    >
                      <AppIcon name="left" size={14} />
                    </button>
                  </Tooltip>
                </div>

                <section className="topology-stage-secondary-panel__section">
                  <div className="topology-stage-secondary-panel__section-head">
                    <span>{"\u8282\u70b9\u7c7b\u578b"}</span>
                    <span>{allNodes.length} {"\u4e2a\u5bf9\u8c61"}</span>
                  </div>
                  <div className="topology-stage-legend-list">
                    {legendItems.map((item) => (
                      <button
                        className={`topology-stage-legend-item ${highlightedLegendType === item.type ? "topology-stage-legend-item--active" : ""}`}
                        key={item.type}
                        onClick={() => handleLegendTypeToggle(item.type)}
                        type="button"
                      >
                        <span
                          aria-hidden="true"
                          className={`topology-stage-legend-item__icon topology-stage-legend-item__icon--${item.type}`}
                        >
                          <img alt="" className="topology-stage-legend-item__icon-image" draggable={false} src={item.iconSrc} />
                        </span>
                        <span className="topology-stage-legend-item__label">{item.label}</span>
                        <span className="topology-stage-legend-item__count">{item.count}</span>
                      </button>
                    ))}
                  </div>
                  {highlightedLegendType ? (
                    <div className="topology-stage-secondary-panel__section-head">
                      <span>{`\u7c7b\u578b\u7b5b\u9009\uff1a${formatTopologyType(highlightedLegendType)}`}</span>
                      <button
                        className="topology-stage-view-switch__option"
                        onClick={() => setHighlightedLegendType(null)}
                        type="button"
                      >
                        {"\u6e05\u9664"}
                      </button>
                    </div>
                  ) : null}
                </section>

                <section className="topology-stage-secondary-panel__section">
                  <div className="topology-stage-secondary-panel__section-head">
                    <span>{"\u89c6\u56fe\u5207\u6362"}</span>
                    <span>
                      {isTreeView
                        ? "\u6811\u7ed3\u6784"
                        : "\u5c42\u5e03\u5c40"}
                    </span>
                  </div>
                  <div aria-label={"\u62d3\u6251\u89c6\u56fe\u5207\u6362"} className="topology-stage-view-switch" role="tablist">
                    {topologyCanvasViewControls.map((control) => {
                      const active = control.id === activeViewControlId;
                      return (
                        <button
                          aria-selected={active}
                          className={`topology-stage-view-switch__option ${active ? "topology-stage-view-switch__option--active" : ""}`}
                          key={control.id}
                          onClick={() =>
                            handleCanvasViewChange(
                              control.viewMode,
                              "layoutPreset" in control ? control.layoutPreset : undefined,
                            )
                          }
                          role="tab"
                          type="button"
                        >
                          {control.label}
                        </button>
                      );
                    })}
                  </div>
                </section>
              </aside>
            ) : (
              <div className="topology-stage-dock topology-stage-dock--secondary-collapsed">
                <Tooltip placement="right" title={"\u5c55\u5f00\u56fe\u4f8b\u9762\u677f"}>
                  <button
                    aria-label={"\u5c55\u5f00\u56fe\u4f8b\u9762\u677f"}
                    className="topology-stage-icon-button"
                    onClick={() => onSecondaryPanelOpenChange(true)}
                    title={"\u5c55\u5f00\u56fe\u4f8b\u9762\u677f"}
                    type="button"
                  >
                    <AppIcon name="layers" size={16} />
                  </button>
                </Tooltip>
              </div>
            )}

            <div className="topology-stage-dock topology-stage-dock--primary" data-testid="topology-action-toolbar">
              <IconToolbarButton
                active={filterPanelOpen && filterPanelEntryMode === "search"}
                icon="search"
                label={"\u641c\u7d22"}
                onClick={() => openFilterPanel("search")}
              />
              <IconToolbarButton
                active={filterPanelOpen && filterPanelEntryMode === "filter"}
                icon="filterFunnel"
                label={"\u7b5b\u9009"}
                onClick={() => toggleFilterPanel("filter")}
              />
              <span aria-hidden="true" className="topology-stage-toolbar__divider" />
              <IconToolbarButton icon="resizeVertical" label={"\u9002\u914d\u753b\u5e03"} onClick={onFitCanvas} />
              <IconToolbarButton icon="targetFocus" label={"\u5b9a\u4f4d\u5f53\u524d\u7126\u70b9"} onClick={onRecenter} />
              {fullscreenSupported ? (
                <IconToolbarButton
                  icon={isFullscreen ? "frameCollapse" : "frameExpand"}
                  label={isFullscreen ? "\u9000\u51fa\u5168\u5c4f" : "\u5168\u5c4f"}
                  onClick={handleToggleFullscreen}
                />
              ) : null}
              <IconToolbarButton icon="refresh" label={"\u91cd\u7f6e\u89c6\u56fe"} onClick={onResetView} />
              <span aria-hidden="true" className="topology-stage-toolbar__divider" />
              <IconToolbarButton icon="minus" label={"\u7f29\u5c0f"} onClick={onZoomOut} />
              <div aria-live="polite" className="topology-stage-toolbar__zoom-badge">
                {zoomPercent}%
              </div>
              <IconToolbarButton icon="plus" label={"\u653e\u5927"} onClick={onZoomIn} />
            </div>

            {filterPanelOpen ? (
              <section className="topology-stage-filter-panel" data-testid="topology-filter-panel">
                <div className="topology-stage-filter-panel__header">
                  <div>
                    <p className="topology-stage-filter-panel__eyebrow">{"\u62d3\u6251\u7b5b\u9009"}</p>
                    <h3 className="topology-stage-filter-panel__title">{"\u641c\u7d22\u4e0e\u6536\u655b\u89c6\u56fe"}</h3>
                  </div>
                  <button
                    aria-label={"\u5173\u95ed\u7b5b\u9009"}
                    className="topology-stage-filter-panel__close"
                    onClick={() => onFilterPanelOpenChange(false)}
                    type="button"
                  >
                    ?
                  </button>
                </div>

                <div className="topology-stage-filter-panel__controls">
                  <AppInput
                    autoFocus={filterPanelEntryMode === "search"}
                    className="topology-stage-filter-panel__search"
                    inputRef={searchInputRef}
                    onChange={onSearchQueryChange}
                    onKeyDown={handleKeyDown}
                    placeholder={"\u641c\u7d22\u673a\u67dc / \u8282\u70b9 / GPU / \u670d\u52a1 / \u4ea4\u6362\u673a"}
                    prefix={<AppIcon name="search" size={16} />}
                    value={searchQuery}
                  />
                  <Select
                    className="app-select topology-stage-filter-panel__select"
                    onChange={(value) => onLayerFilterChange(value)}
                    options={layerOptions}
                    value={layerFilter}
                  />
                  {searchQuery.trim() ? (
                    <div className="topology-stage-filter-panel__action-row">
                      <AppButton onClick={onSearchSubmit} size="sm" variant="secondary">
                        {"\u805a\u7126\u9996\u4e2a\u7ed3\u679c"}
                      </AppButton>
                    </div>
                  ) : null}
                </div>

                {searchStatus ? (
                  <div className="topology-stage-filter-panel__status">
                    <StatusChip tone={searchStatus.tone}>{searchStatus.label}</StatusChip>
                  </div>
                ) : null}

                <div className="topology-stage-filter-panel__results">
                  <div className="topology-stage-filter-panel__results-header">
                    <span>{"\u7ed3\u679c\u5217\u8868"}</span>
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
                              {formatTopologyType(node.type)} ? {formatTopologyStatus(node.status)}
                            </span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="topology-stage-filter-panel__empty">{"\u5f53\u524d\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u5339\u914d\u5bf9\u8c61\u3002"}</p>
                    )
                  ) : (
                    <p className="topology-stage-filter-panel__empty">
                      {"\u8f93\u5165\u5bf9\u8c61\u540d\u79f0\u540e\uff0c\u753b\u5e03\u4f1a\u6536\u655b\u5230\u547d\u4e2d\u5bf9\u8c61\u53ca\u5176\u76f4\u63a5\u5173\u8054\u5bf9\u8c61\u3002"}
                    </p>
                  )}
                </div>
              </section>
            ) : null}
          </>
        ) : null}

        {isLoading ? (
          <div className="state-block">
            <Spin />
          </div>
        ) : error ? (
          <EmptyState>{error}</EmptyState>
        ) : !hasSourceData ? (
          <EmptyState>{"\u5f53\u524d\u6ca1\u6709\u62d3\u6251\u6570\u636e\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002"}</EmptyState>
        ) : isGraphEmpty ? (
          <EmptyState>{"\u5f53\u524d\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u53ef\u5c55\u793a\u5bf9\u8c61\uff0c\u8bf7\u8c03\u6574\u7b5b\u9009\u6216\u91cd\u7f6e\u89c6\u56fe\u3002"}</EmptyState>
        ) : isTreeEmpty ? (
          <EmptyState>{"\u5f53\u524d\u6ca1\u6709\u53ef\u5c55\u793a\u7684\u6811\u89c6\u56fe\u6570\u636e\uff0c\u8bf7\u5207\u56de\u5173\u7cfb\u56fe\u67e5\u770b\u3002"}</EmptyState>
        ) : isTreeView ? (
          <TopologyTreeView onSelectNode={handleResultSelect} selectedNodeId={selectedNodeId} tree={tree} />
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
            highlightedTypes={highlightedLegendType ? [highlightedLegendType] : undefined}
            expandedAggregateIds={expandedAggregateIds}
            expandedAggregateMeta={expandedAggregateMeta}
            onCollapseAggregate={onCollapseAggregate}
            onCanvasInteraction={() => setNodeActions(null)}
            onHoverNode={onHoverNode}
            onOpenNodeActions={(payload) => {
              onFilterPanelOpenChange(false);
              if (
                variant === "modified" &&
                payload.trigger === "click" &&
                (isSyntheticServiceAggregateNode(payload.node) || isSyntheticGpuAggregateNode(payload.node) || isSyntheticBmcAggregateNode(payload.node))
              ) {
                setNodeActions(null);
                onToggleAggregateNode?.(payload.node.id);
                return;
              }
              if (
                variant === "modified" &&
                payload.trigger === "hover" &&
                (isSyntheticServiceAggregateNode(payload.node) || isSyntheticGpuAggregateNode(payload.node) || isSyntheticBmcAggregateNode(payload.node))
              ) {
                setNodeActions(null);
                return;
              }

              setNodeActions(payload);
            }}
            onSelectNode={handleCanvasSelect}
            onZoomChange={setZoomPercent}
            selectedNodeId={selectedNodeId}
            viewInsets={canvasViewInsets}
            variant={variant}
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
            <StatusChip tone={getStatusTone(actionNode.status)}>
              {formatTopologyStatus(actionNode.status)}
            </StatusChip>
          </div>
          <dl className="topology-node-menu__facts">
            <div>
              <dt>{"\u4f4d\u7f6e"}</dt>
              <dd>{getLocationLabel(actionNode) || "\u672a\u63d0\u4f9b"}</dd>
            </div>
            <div>
              <dt>{"\u6458\u8981"}</dt>
              <dd>{actionNode.summary}</dd>
            </div>
          </dl>
          <div className="topology-node-menu__actions">
            <AppButton
              onClick={() => {
                onOpenObjectTopology(actionNode.id, "isolate");
                setNodeActions(null);
              }}
              size="sm"
              variant="secondary"
            >
              Isolate
            </AppButton>
            <AppButton
              onClick={() => {
                onOpenObjectTopology(actionNode.id, "default");
                setNodeActions(null);
              }}
              size="sm"
              variant="primary"
            >
              View topology
            </AppButton>
          </div>
          {actionNode && nodeActions?.trigger === "click" && selectedNode && selectedNode.id === actionNode.id ? (
            <p className="topology-node-menu__hint">{"\u5f53\u524d\u5bf9\u8c61\u5df2\u88ab\u9009\u4e2d\uff0c\u53f3\u952e\u5916\u7a7a\u767d\u533a\u57df\u53ef\u5173\u95ed\u6b64\u6d6e\u5c42\u3002"}</p>
          ) : actionNode && nodeActions?.trigger === "hover" ? (
            <p className="topology-node-menu__hint">{"\u6eda\u8f6e\u6216\u79fb\u52a8\u753b\u5e03\u4e0d\u4f1a\u9501\u5b9a\u6b64\u6d6e\u5c42\uff0c\u70b9\u51fb\u8282\u70b9\u53ef\u56fa\u5b9a\u64cd\u4f5c\u9762\u677f\u3002"}</p>
          ) : null}
        </aside>
      ) : null}
    </div>
  );
}

export default TopologyExplorer;
