import { create } from "zustand";

import { apiClient } from "../../api/client";
import type { TopologyExplorerResponse } from "../../api/types";
import { getImpactPathIdForNode, getPrimaryImpactPathId, searchTopologyObjects } from "./selectors";
import type {
  DrawerTabKey,
  ExplorerLayerFilter,
  ExplorerLayoutPreset,
  ExplorerStatusFilter,
  ExplorerSummaryFilter,
  ExplorerViewMode,
  InspectorTabKey,
  SearchFeedback,
} from "./types";

type TopologyExplorerStore = {
  data?: TopologyExplorerResponse;
  isLoading: boolean;
  error?: string;
  viewMode: ExplorerViewMode;
  selectedNodeId?: string;
  activeImpactPathId?: string;
  inspectorOpen: boolean;
  searchQuery: string;
  matchedNodeIds: string[];
  searchFeedback: SearchFeedback;
  statusFilter: ExplorerStatusFilter;
  layerFilter: ExplorerLayerFilter;
  summaryFilter: ExplorerSummaryFilter;
  legendOpen: boolean;
  drawerOpen: boolean;
  layoutPreset: ExplorerLayoutPreset;
  hoveredNodeId?: string;
  inspectorTab: InspectorTabKey;
  drawerTab: DrawerTabKey;
  fetchTopologyExplorer: () => Promise<void>;
  setViewMode: (viewMode: ExplorerViewMode) => void;
  setSelectedNodeId: (selectedNodeId?: string) => void;
  setActiveImpactPathId: (activeImpactPathId?: string) => void;
  setInspectorOpen: (inspectorOpen: boolean) => void;
  setSearchQuery: (searchQuery: string) => void;
  locateSearchResult: () => string | undefined;
  setStatusFilter: (statusFilter: ExplorerStatusFilter) => void;
  setLayerFilter: (layerFilter: ExplorerLayerFilter) => void;
  applySummaryFilter: (summaryFilter: ExplorerSummaryFilter) => void;
  setLegendOpen: (legendOpen: boolean) => void;
  toggleDrawer: () => void;
  setLayoutPreset: (layoutPreset: ExplorerLayoutPreset) => void;
  cycleLayoutPreset: () => void;
  setHoveredNodeId: (hoveredNodeId?: string) => void;
  setInspectorTab: (inspectorTab: InspectorTabKey) => void;
  setDrawerTab: (drawerTab: DrawerTabKey) => void;
  resetExplorerView: () => void;
};

function resolveSearchMatches(data: TopologyExplorerResponse | undefined, searchQuery: string) {
  if (!data) {
    return [];
  }

  return searchTopologyObjects(data.nodes, searchQuery).map((node) => node.id);
}

export const useTopologyExplorerStore = create<TopologyExplorerStore>((set, get) => ({
  data: undefined,
  isLoading: false,
  error: undefined,
  viewMode: "graph",
  selectedNodeId: undefined,
  activeImpactPathId: undefined,
  inspectorOpen: true,
  searchQuery: "",
  matchedNodeIds: [],
  searchFeedback: "idle",
  statusFilter: "all",
  layerFilter: "all",
  summaryFilter: "all",
  legendOpen: false,
  drawerOpen: false,
  layoutPreset: "layered",
  hoveredNodeId: undefined,
  inspectorTab: "overview",
  drawerTab: "paths",
  fetchTopologyExplorer: async () => {
    set({ isLoading: true, error: undefined });

    try {
      const data = await apiClient.getTopologyExplorer();
      set((state) => ({
        data,
        isLoading: false,
        activeImpactPathId: state.activeImpactPathId ?? getPrimaryImpactPathId(data),
        matchedNodeIds: resolveSearchMatches(data, state.searchQuery),
        searchFeedback: state.searchQuery ? "ready" : "idle",
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载拓扑失败";
      set({ error: message, isLoading: false });
    }
  },
  setViewMode: (viewMode) =>
    set((state) => {
      if (viewMode !== "impact") {
        return { viewMode };
      }

      const nextImpactPathId =
        getImpactPathIdForNode(state.data?.paths ?? [], state.selectedNodeId) ??
        state.activeImpactPathId ??
        getPrimaryImpactPathId(state.data);

      return {
        viewMode,
        activeImpactPathId: nextImpactPathId,
      };
    }),
  setSelectedNodeId: (selectedNodeId) =>
    set((state) => ({
      selectedNodeId,
      activeImpactPathId:
        getImpactPathIdForNode(state.data?.paths ?? [], selectedNodeId) ?? state.activeImpactPathId,
      inspectorOpen: selectedNodeId ? state.inspectorOpen : false,
      inspectorTab: "overview",
      drawerTab: "paths",
    })),
  setActiveImpactPathId: (activeImpactPathId) => set({ activeImpactPathId }),
  setInspectorOpen: (inspectorOpen) => set({ inspectorOpen }),
  setSearchQuery: (searchQuery) =>
    set((state) => ({
      searchQuery,
      matchedNodeIds: resolveSearchMatches(state.data, searchQuery),
      searchFeedback: searchQuery ? "ready" : "idle",
    })),
  locateSearchResult: () => {
    const { data, matchedNodeIds, searchQuery } = get();
    if (!data || !searchQuery.trim()) {
      set({ searchFeedback: "idle" });
      return undefined;
    }

      const firstMatchId = matchedNodeIds[0];
    if (!firstMatchId) {
      set({ searchFeedback: "not_found" });
      return undefined;
    }

    set({
      selectedNodeId: firstMatchId,
      activeImpactPathId: getImpactPathIdForNode(data.paths, firstMatchId) ?? get().activeImpactPathId,
      viewMode: "graph",
      inspectorOpen: true,
      searchFeedback: "ready",
    });
    return firstMatchId;
  },
  setStatusFilter: (statusFilter) => set({ statusFilter }),
  setLayerFilter: (layerFilter) => set({ layerFilter }),
  applySummaryFilter: (summaryFilter) =>
    set((state) => {
      const data = state.data;
      if (!data || summaryFilter === "all") {
        return {
          summaryFilter,
          statusFilter: "all" as const,
          viewMode: "graph" as const,
          activeImpactPathId: state.activeImpactPathId ?? getPrimaryImpactPathId(data),
        };
      }

      if (summaryFilter === "abnormal") {
        const abnormalNode = data.nodes.find((node) => node.status === "abnormal");
        return {
          summaryFilter,
          statusFilter: "abnormal" as const,
          viewMode: "graph" as const,
          activeImpactPathId:
            getImpactPathIdForNode(data.paths, abnormalNode?.id ?? state.selectedNodeId) ?? state.activeImpactPathId,
          inspectorOpen: true,
          selectedNodeId: abnormalNode?.id ?? state.selectedNodeId,
        };
      }

      if (summaryFilter === "impacted") {
        const impactedNode = data.nodes.find((node) => node.status === "impacted");
        return {
          summaryFilter,
          statusFilter: "all" as const,
          viewMode: "graph" as const,
          activeImpactPathId:
            getImpactPathIdForNode(data.paths, impactedNode?.id ?? state.selectedNodeId) ?? state.activeImpactPathId,
          inspectorOpen: true,
          selectedNodeId: impactedNode?.id ?? state.selectedNodeId,
        };
      }

      const activePathId =
        getImpactPathIdForNode(data.paths, state.selectedNodeId) ??
        state.activeImpactPathId ??
        getPrimaryImpactPathId(data);
      const activePath = data.paths.find((path) => path.id === activePathId);
      return {
        summaryFilter,
        statusFilter: "all" as const,
        viewMode: "impact" as const,
        activeImpactPathId: activePathId,
        inspectorOpen: true,
        selectedNodeId: activePath?.rootCauseNodeId ?? state.selectedNodeId,
      };
    }),
  setLegendOpen: (legendOpen) => set({ legendOpen }),
  toggleDrawer: () => set((state) => ({ drawerOpen: !state.drawerOpen })),
  setLayoutPreset: (layoutPreset) => set({ layoutPreset }),
  cycleLayoutPreset: () =>
    set((state) => ({
      layoutPreset: state.layoutPreset === "layered" ? "domain" : "layered",
    })),
  setHoveredNodeId: (hoveredNodeId) => set({ hoveredNodeId }),
  setInspectorTab: (inspectorTab) => set({ inspectorTab }),
  setDrawerTab: (drawerTab) => set({ drawerTab }),
  resetExplorerView: () =>
    set({
      viewMode: "graph",
      selectedNodeId: undefined,
      activeImpactPathId: undefined,
      inspectorOpen: true,
      searchQuery: "",
      matchedNodeIds: [],
      searchFeedback: "idle",
      statusFilter: "all",
      layerFilter: "all",
      summaryFilter: "all",
      legendOpen: false,
      drawerOpen: false,
      layoutPreset: "layered",
      hoveredNodeId: undefined,
      inspectorTab: "overview",
      drawerTab: "paths",
    }),
}));
