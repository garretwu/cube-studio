import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "../api/client";
import type { WSEvent } from "../api/types";
import { useAlertStore } from "../store/alertStore";
import { useAlertsRealtimeSync } from "./useAlertsRealtimeSync";

let websocketState: "connecting" | "open" | "closed" | "error" = "closed";
let emitWebsocketEvent: ((event: WSEvent) => void) | undefined;

vi.mock("./useWebSocket", () => ({
  useWebSocket: (
    _url: string,
    onEvent: (event: WSEvent) => void,
    options: { enabled?: boolean } = {},
  ) => {
    emitWebsocketEvent = onEvent;
    if (options.enabled === false) {
      return { state: "closed" as const };
    }
    return { state: websocketState };
  },
}));

const alertsPayload = {
  alerts: [
    {
      alert_name: "RealtimeAlert",
      severity: "warning" as const,
      labels: { instance: "node-2" },
      annotations: { summary: "realtime" },
      starts_at: "2026-03-18T13:00:00Z",
      fingerprint: "fp-realtime-1",
      status: "firing" as const,
      source: "alertmanager",
    },
  ],
  clusters: [{ cluster_id: "cluster-rt", summary: "realtime", severity: "warning" as const, alerts: ["fp-realtime-1"] }],
};

function HookHarness() {
  useAlertsRealtimeSync();
  return null;
}

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

describe("useAlertsRealtimeSync", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    websocketState = "closed";
    emitWebsocketEvent = undefined;
    vi.stubEnv("VITE_API_TOKEN", "test-token");
    vi.spyOn(apiClient, "getAlerts").mockResolvedValue(alertsPayload);
    resetStore();
  });

  afterEach(() => {
    useAlertStore.getState().stopReconcile();
    vi.unstubAllEnvs();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("syncs immediately when websocket becomes open and keeps reconciling every 60s", async () => {
    const view = render(<HookHarness />);

    websocketState = "open";
    view.rerender(<HookHarness />);

    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(1);
    expect(useAlertStore.getState().wsState).toBe("open");
    expect(useAlertStore.getState().realtimeEnabled).toBe(true);

    emitWebsocketEvent?.({
      schema_version: "1.0",
      type: "alert",
      session_id: "alerts",
      timestamp: "2026-03-18T13:01:00Z",
      data: { action: "upsert" },
    });
    await vi.advanceTimersByTimeAsync(350);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(3);

    view.unmount();
  });

  it("disables realtime sync when token is missing", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    render(<HookHarness />);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiClient.getAlerts).toHaveBeenCalledTimes(0);
    expect(useAlertStore.getState().realtimeEnabled).toBe(false);
    expect(useAlertStore.getState().wsState).toBe("closed");
  });
});
