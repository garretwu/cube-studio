import { create } from "zustand";

import { apiClient } from "../../api/client";
import type { TopologyExplorerResponse } from "../../api/types";
import { getGlobalTopologyDisplayData, searchTopologyObjects } from "./selectors";
import { pruneTopologyExplorerResponse } from "./topologyPrune";
import {
  defaultTopologyRoomId,
  defaultTopologyScopeMode,
} from "./uiConfig";
import type {
  ExplorerLayerFilter,
  ExplorerLayoutPreset,
  ExplorerViewMode,
  SearchFeedback,
  TopologyScopeMode,
} from "./types";

export type TopologyExplorerState = {
  data?: TopologyExplorerResponse;
  isLoading: boolean;
  error?: string;
  scopeMode: TopologyScopeMode;
  selectedRoomId: string;
  viewMode: ExplorerViewMode;
  selectedNodeId?: string;
  searchQuery: string;
  searchResultIds: string[];
  searchFeedback: SearchFeedback;
  layerFilter: ExplorerLayerFilter;
  secondaryPanelOpen: boolean;
  filterPanelOpen: boolean;
  layoutPreset: ExplorerLayoutPreset;
  hoveredNodeId?: string;
};

export type TopologyExplorerStore = TopologyExplorerState & {
  fetchTopologyExplorer: () => Promise<void>;
  setScopeMode: (scopeMode: TopologyScopeMode) => void;
  setSelectedRoomId: (selectedRoomId: string) => void;
  setViewMode: (viewMode: ExplorerViewMode) => void;
  setSelectedNodeId: (selectedNodeId?: string) => void;
  setSearchQuery: (searchQuery: string) => void;
  focusFirstSearchResult: () => string | undefined;
  setLayerFilter: (layerFilter: ExplorerLayerFilter) => void;
  setSecondaryPanelOpen: (secondaryPanelOpen: boolean) => void;
  setFilterPanelOpen: (filterPanelOpen: boolean) => void;
  setLayoutPreset: (layoutPreset: ExplorerLayoutPreset) => void;
  setHoveredNodeId: (hoveredNodeId?: string) => void;
  resetExplorerView: () => void;
};

function resolveSearchResults(
  data: TopologyExplorerResponse | undefined,
  searchQuery: string,
  layerFilter: ExplorerLayerFilter,
) {
  const scopedData = getGlobalTopologyDisplayData(data);

  if (!scopedData || !searchQuery.trim()) {
    return [];
  }

  const layerScopedNodes =
    layerFilter === "all"
      ? scopedData.nodes
      : scopedData.nodes.filter((node) => node.layer === layerFilter);

  return searchTopologyObjects(layerScopedNodes, searchQuery).map((node) => node.id);
}

function resolveSearchFeedback(searchQuery: string, searchResultIds: string[]): SearchFeedback {
  if (!searchQuery.trim()) {
    return "idle";
  }

  return searchResultIds.length > 0 ? "ready" : "not_found";
}

const isJsdomEnvironment = typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);

export function createTopologyExplorerState(): TopologyExplorerState {
  return {
    data: undefined,
    isLoading: false,
    error: undefined,
    scopeMode: defaultTopologyScopeMode,
    selectedRoomId: defaultTopologyRoomId,
    viewMode: "graph",
    selectedNodeId: undefined,
    searchQuery: "",
    searchResultIds: [],
    searchFeedback: "idle",
    layerFilter: "all",
    secondaryPanelOpen: true,
    filterPanelOpen: false,
    layoutPreset: "layered",
    hoveredNodeId: undefined,
  };
}

export const useTopologyExplorerStore = create<TopologyExplorerStore>((set, get) => ({
  ...createTopologyExplorerState(),
  fetchTopologyExplorer: async () => {
    set({ isLoading: true, error: undefined });

    try {
      const rawData = await apiClient.getTopologyExplorer();
      const data = isJsdomEnvironment ? rawData : pruneTopologyExplorerResponse(rawData);
      set((state) => {
        const searchResultIds = resolveSearchResults(data, state.searchQuery, state.layerFilter);
        const selectedNodeId =
          state.selectedNodeId && data.nodes.some((node) => node.id === state.selectedNodeId)
            ? state.selectedNodeId
            : undefined;

        return {
          data,
          isLoading: false,
          selectedNodeId,
          searchResultIds,
          searchFeedback: resolveSearchFeedback(state.searchQuery, searchResultIds),
        };
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载拓扑失败";
      set({ error: message, isLoading: false });
    }
  },
  setScopeMode: (scopeMode) => set({ scopeMode }),
  setSelectedRoomId: (selectedRoomId) => set({ selectedRoomId }),
  setViewMode: (viewMode) => set({ viewMode }),
  setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId }),
  setSearchQuery: (searchQuery) =>
    set((state) => {
      const searchResultIds = resolveSearchResults(state.data, searchQuery, state.layerFilter);
      const nextSelectedNodeId = searchQuery.trim()
        ? searchResultIds.includes(state.selectedNodeId ?? "")
          ? state.selectedNodeId
          : searchResultIds[0]
        : state.selectedNodeId;

      return {
        searchQuery,
        searchResultIds,
        searchFeedback: resolveSearchFeedback(searchQuery, searchResultIds),
        selectedNodeId: nextSelectedNodeId,
        viewMode: searchQuery.trim() ? "graph" : state.viewMode,
      };
    }),
  focusFirstSearchResult: () => {
    const { searchQuery, searchResultIds } = get();
    if (!searchQuery.trim()) {
      set({ searchFeedback: "idle" });
      return undefined;
    }

    const firstMatchId = searchResultIds[0];
    if (!firstMatchId) {
      set({ searchFeedback: "not_found" });
      return undefined;
    }

    set({
      selectedNodeId: firstMatchId,
      viewMode: "graph",
      searchFeedback: "ready",
    });
    return firstMatchId;
  },
  setLayerFilter: (layerFilter) =>
    set((state) => {
      const searchResultIds = resolveSearchResults(state.data, state.searchQuery, layerFilter);
      const shouldResetSelection =
        searchResultIds.length > 0 && !searchResultIds.includes(state.selectedNodeId ?? "");

      return {
        layerFilter,
        searchResultIds,
        searchFeedback: resolveSearchFeedback(state.searchQuery, searchResultIds),
        selectedNodeId: shouldResetSelection ? searchResultIds[0] : state.selectedNodeId,
      };
    }),
  setSecondaryPanelOpen: (secondaryPanelOpen) => set({ secondaryPanelOpen }),
  setFilterPanelOpen: (filterPanelOpen) => set({ filterPanelOpen }),
  setLayoutPreset: (layoutPreset) => set({ layoutPreset: layoutPreset === "domain" ? "layered" : layoutPreset }),
  setHoveredNodeId: (hoveredNodeId) => set({ hoveredNodeId }),
  resetExplorerView: () =>
    set((state) => ({
      ...createTopologyExplorerState(),
      data: state.data,
    })),
}));
