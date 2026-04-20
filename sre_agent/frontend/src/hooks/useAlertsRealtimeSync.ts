import { useCallback, useEffect, useMemo, useRef } from "react";

import type { WSEvent } from "../api/types";
import { getAccessTokenSync, hasAccessToken, refreshAccessToken } from "../auth/tokenManager";
import { buildBackendWsUrl } from "../api/ws";
import { useAlertStore } from "../store/alertStore";
import { useWebSocket } from "./useWebSocket";

const RECONCILE_INTERVAL_MS = 60_000;

export function useAlertsRealtimeSync() {
  const fetchAlerts = useAlertStore((state) => state.fetchAlerts);
  const handleAlertRealtimeEvent = useAlertStore((state) => state.handleAlertRealtimeEvent);
  const startReconcile = useAlertStore((state) => state.startReconcile);
  const stopReconcile = useAlertStore((state) => state.stopReconcile);
  const setRealtimeEnabled = useAlertStore((state) => state.setRealtimeEnabled);
  const setRealtimeWsState = useAlertStore((state) => state.setRealtimeWsState);

  const realtimeEnabled = hasAccessToken();

  const websocketUrl = useMemo(
    () =>
      buildBackendWsUrl("/ws/alerts", {
      }),
    [],
  );

  const onEvent = useCallback(
    (event: WSEvent) => {
      handleAlertRealtimeEvent(event);
    },
    [handleAlertRealtimeEvent],
  );

  const ws = useWebSocket(websocketUrl, onEvent, {
    enabled: realtimeEnabled,
    maxBufferedMessages: 400,
    getToken: () => getAccessTokenSync(),
    onAuthFailure: async () => {
      await refreshAccessToken();
    },
  });

  useEffect(() => {
    setRealtimeEnabled(realtimeEnabled);
  }, [realtimeEnabled, setRealtimeEnabled]);

  useEffect(() => {
    setRealtimeWsState(realtimeEnabled ? ws.state : "closed");
  }, [realtimeEnabled, setRealtimeWsState, ws.state]);

  const previousWsState = useRef<"connecting" | "open" | "closed" | "error">("closed");
  useEffect(() => {
    const previous = previousWsState.current;
    if (realtimeEnabled && ws.state === "open" && previous !== "open") {
      void fetchAlerts();
    }
    previousWsState.current = ws.state;
  }, [fetchAlerts, realtimeEnabled, ws.state]);

  useEffect(() => {
    if (!realtimeEnabled) {
      stopReconcile();
      return undefined;
    }
    startReconcile(RECONCILE_INTERVAL_MS);
    return () => {
      stopReconcile();
    };
  }, [realtimeEnabled, startReconcile, stopReconcile]);
}
