import { create } from "zustand";

import { apiClient } from "../api/client";
import type { Alert, AlertCluster, Severity } from "../api/types";

type AlertState = {
  alerts: Alert[];
  clusters: AlertCluster[];
  severityFilter: Severity | "all";
  isLoading: boolean;
  fetchAlerts: () => Promise<void>;
  setSeverityFilter: (value: Severity | "all") => void;
};

export const useAlertStore = create<AlertState>((set, get) => ({
  alerts: [],
  clusters: [],
  severityFilter: "all",
  isLoading: false,
  fetchAlerts: async () => {
    const current = get();
    if (current.isLoading) {
      return;
    }
    set({ isLoading: true });
    const response = await apiClient.getAlerts();
    set({ alerts: response.alerts, clusters: response.clusters, isLoading: false });
  },
  setSeverityFilter: (severityFilter) => set({ severityFilter }),
}));
