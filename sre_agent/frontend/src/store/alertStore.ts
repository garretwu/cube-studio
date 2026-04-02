import { create } from "zustand";

import { apiClient } from "../api/client";
import type { Alert, AlertCluster, Severity, WSEvent } from "../api/types";

const ALERT_EVENT_DEBOUNCE_MS = 350;
const DEFAULT_RECONCILE_INTERVAL_MS = 60_000;

let reconcileTimer: ReturnType<typeof globalThis.setInterval> | null = null;
let alertRefreshTimer: ReturnType<typeof globalThis.setTimeout> | null = null;

function clearReconcileTimer() {
  if (reconcileTimer === null) {
    return;
  }
  globalThis.clearInterval(reconcileTimer);
  reconcileTimer = null;
}

function clearAlertRefreshTimer() {
  if (alertRefreshTimer === null) {
    return;
  }
  globalThis.clearTimeout(alertRefreshTimer);
  alertRefreshTimer = null;
}

type WsState = "connecting" | "open" | "closed" | "error";

type AlertState = {
  alerts: Alert[];
  clusters: AlertCluster[];
  severityFilter: Severity | "all";
  wsState: WsState;
  lastAlertEventAt?: string;
  lastSnapshotSyncAt?: string;
  realtimeEnabled: boolean;
  isLoading: boolean;
  hasLoaded: boolean;
  error?: string;
  fetchAlerts: () => Promise<void>;
  handleAlertRealtimeEvent: (event: WSEvent) => void;
  startReconcile: (intervalMs?: number) => void;
  stopReconcile: () => void;
  setRealtimeWsState: (state: WsState) => void;
  setRealtimeEnabled: (enabled: boolean) => void;
  setSeverityFilter: (value: Severity | "all") => void;
};

export const useAlertStore = create<AlertState>((set, get) => ({
  alerts: [],
  clusters: [],
  severityFilter: "all",
  wsState: "closed",
  lastAlertEventAt: undefined,
  lastSnapshotSyncAt: undefined,
  realtimeEnabled: false,
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
        lastSnapshotSyncAt: new Date().toISOString(),
        error: undefined,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load alerts";
      set({ alerts: [], clusters: [], isLoading: false, hasLoaded: true, error: message });
    }
  },
  handleAlertRealtimeEvent: (event) => {
    if (event.type !== "alert") {
      return;
    }
    const eventTimestamp =
      typeof event.timestamp === "string" && event.timestamp.trim()
        ? event.timestamp
        : new Date().toISOString();
    set({ lastAlertEventAt: eventTimestamp });
    clearAlertRefreshTimer();
    alertRefreshTimer = globalThis.setTimeout(() => {
      alertRefreshTimer = null;
      void get().fetchAlerts();
    }, ALERT_EVENT_DEBOUNCE_MS);
  },
  startReconcile: (intervalMs = DEFAULT_RECONCILE_INTERVAL_MS) => {
    const safeIntervalMs = Number.isFinite(intervalMs) ? Math.max(1000, Math.floor(intervalMs)) : DEFAULT_RECONCILE_INTERVAL_MS;
    clearReconcileTimer();
    reconcileTimer = globalThis.setInterval(() => {
      void get().fetchAlerts();
    }, safeIntervalMs);
  },
  stopReconcile: () => {
    clearReconcileTimer();
    clearAlertRefreshTimer();
  },
  setRealtimeWsState: (wsState) => set({ wsState }),
  setRealtimeEnabled: (realtimeEnabled) => set({ realtimeEnabled }),
  setSeverityFilter: (severityFilter) => set({ severityFilter }),
}));
