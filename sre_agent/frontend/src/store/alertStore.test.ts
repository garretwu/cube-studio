import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "../api/client";
import type { WSEvent } from "../api/types";
import { useAlertStore } from "./alertStore";

const alertsPayload = {
  alerts: [
    {
      alert_name: "TestAlert",
      severity: "warning" as const,
      labels: { instance: "node-1" },
      annotations: { summary: "test" },
      starts_at: "2026-03-18T12:00:00Z",
      fingerprint: "fp-test-1",
      status: "firing" as const,
      source: "alertmanager",
    },
  ],
  clusters: [{ cluster_id: "cluster-1", summary: "test", severity: "warning" as const, alerts: ["fp-test-1"] }],
};

function resetStore() {
  useAlertStore.getState().stopReconcile();
  useAlertStore.setState({
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
  });
}

describe("alertStore realtime sync", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(apiClient, "getAlerts").mockResolvedValue(alertsPayload);
    resetStore();
  });

  afterEach(() => {
    useAlertStore.getState().stopReconcile();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("debounces alert events and refreshes snapshot once", async () => {
    const event: WSEvent = {
      schema_version: "1.0",
      type: "alert",
      session_id: "alerts",
      timestamp: "2026-03-18T12:01:00Z",
      data: { action: "upsert" },
    };

    useAlertStore.getState().handleAlertRealtimeEvent(event);
    useAlertStore.getState().handleAlertRealtimeEvent({
      ...event,
      timestamp: "2026-03-18T12:01:01Z",
      data: { action: "remove" },
    });

    expect(apiClient.getAlerts).toHaveBeenCalledTimes(0);

    await vi.advanceTimersByTimeAsync(349);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(0);

    await vi.advanceTimersByTimeAsync(1);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(1);
    expect(useAlertStore.getState().lastAlertEventAt).toBe("2026-03-18T12:01:01Z");
    expect(useAlertStore.getState().alerts).toHaveLength(1);
    expect(useAlertStore.getState().lastSnapshotSyncAt).toBeTruthy();
  });

  it("runs reconcile refresh every interval and stops after stopReconcile", async () => {
    useAlertStore.getState().startReconcile(60_000);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(2);

    useAlertStore.getState().stopReconcile();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(2);
  });

  it("does not issue duplicated requests while loading", async () => {
    useAlertStore.setState({ isLoading: true });
    useAlertStore.getState().startReconcile(60_000);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(0);
  });
});

