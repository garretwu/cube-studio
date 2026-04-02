import { create } from "zustand";

import { apiClient } from "../api/client";
import type { Alert, AlertCluster, Severity } from "../api/types";

type AlertState = {
  alerts: Alert[];
  clusters: AlertCluster[];
  severityFilter: Severity | "all";
  isLoading: boolean;
  hasLoaded: boolean;
  error?: string;
  fetchAlerts: () => Promise<void>;
  setSeverityFilter: (value: Severity | "all") => void;
};

export const useAlertStore = create<AlertState>((set, get) => ({
  alerts: [],
  clusters: [],
  severityFilter: "all",
  isLoading: false,
  hasLoaded: false,
  error: undefined,
  fetchAlerts: async () => {
    const current = get();
    if (current.isLoading) {
      return;
    }
    set({ isLoading: true, error: undefined });
    try {
      const response = await apiClient.getAlerts();
      set({
        alerts: response.alerts,
        clusters: response.clusters,
        isLoading: false,
        hasLoaded: true,
        error: undefined,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load alerts";
      set({ alerts: [], clusters: [], isLoading: false, hasLoaded: true, error: message });
    }
  },
  setSeverityFilter: (severityFilter) => set({ severityFilter }),
}));
