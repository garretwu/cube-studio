import { create } from "zustand";

import { apiClient } from "../api/client";
import type { OntologyEdge, OntologyNode } from "../api/types";

type TopologyState = {
  nodes: OntologyNode[];
  edges: OntologyEdge[];
  activeAlerts: number;
  recentEvents: string[];
  error?: string;
  selectedNodeId?: string;
  isLoading: boolean;
  fetchTopology: () => Promise<void>;
  selectNode: (nodeId?: string) => void;
};

export const useTopologyStore = create<TopologyState>((set) => ({
  nodes: [],
  edges: [],
  activeAlerts: 0,
  recentEvents: [],
  error: undefined,
  selectedNodeId: undefined,
  isLoading: false,
  fetchTopology: async () => {
    set({ isLoading: true, error: undefined });
    try {
      const response = await apiClient.getTopology();
      set({
        nodes: response.nodes,
        edges: response.edges,
        activeAlerts: response.active_alerts,
        recentEvents: response.recent_events,
        isLoading: false,
        error: undefined,
        selectedNodeId: response.nodes[0]?.id,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load topology";
      set({
        nodes: [],
        edges: [],
        activeAlerts: 0,
        recentEvents: [],
        selectedNodeId: undefined,
        isLoading: false,
        error: message,
      });
    }
  },
  selectNode: (selectedNodeId) => set({ selectedNodeId }),
}));
