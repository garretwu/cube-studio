import { create } from "zustand";

import { apiClient } from "../api/client";
import type { LoopResult, RemediationOverview, SessionEvent, WSEvent } from "../api/types";
import { getPrimaryPlanKey } from "../pages/rootCauseModel";

type RealtimeState = "connecting" | "open" | "closed" | "error";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeEventId(value: unknown): string | undefined {
  if (typeof value === "string" || typeof value === "number") {
    const normalized = String(value).trim();
    if (normalized) {
      return normalized;
    }
  }
  return undefined;
}

function getEventId(event: Pick<SessionEvent, "data">): string | undefined {
  return isRecord(event.data) ? normalizeEventId(event.data.event_id) : undefined;
}

function getEventStage(event: SessionEvent): string {
  if (!isRecord(event.data)) {
    return "";
  }
  const stage = event.data.stage;
  return typeof stage === "string" ? stage.trim().toLowerCase() : "";
}

function getEventDedupeKey(event: SessionEvent): string {
  const eventId = getEventId(event);
  if (eventId) {
    return `event_id:${eventId}`;
  }
  return `fallback:${event.type}:${event.timestamp}:${getEventStage(event)}`;
}

function mergeSessionEvents(current: SessionEvent[], incoming: SessionEvent[]): SessionEvent[] {
  if (incoming.length === 0) {
    return current;
  }
  const existingKeys = new Set(current.map((event) => getEventDedupeKey(event)));
  const merged = [...current];
  incoming.forEach((event) => {
    const dedupeKey = getEventDedupeKey(event);
    if (existingKeys.has(dedupeKey)) {
      return;
    }
    existingKeys.add(dedupeKey);
    merged.push(event);
  });
  return merged;
}

function getLastEventId(events: SessionEvent[]): string | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const eventId = getEventId(events[index]);
    if (eventId) {
      return eventId;
    }
  }
  return undefined;
}

function derivePlanKeyFromEvents(events: SessionEvent[]): string | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const data = isRecord(events[index]?.data) ? events[index].data : {};
    const explicit = typeof data.plan_key === "string" ? data.plan_key.trim() : "";
    if (explicit) {
      return explicit;
    }
    const planKeys = Array.isArray(data.plan_keys)
      ? data.plan_keys
          .map((item) => (typeof item === "string" ? item.trim() : ""))
          .filter((item) => item.length > 0)
      : [];
    if (planKeys.length > 0) {
      return planKeys[0];
    }
  }
  return undefined;
}

type RemediationState = {
  loop?: LoopResult;
  overview?: RemediationOverview;
  events: SessionEvent[];
  sessionId: string;
  lastEventId?: string;
  realtimeState: RealtimeState;
  isLoading: boolean;
  approvalDialogOpen: boolean;
  setSessionId: (sessionId: string) => void;
  setRealtimeState: (state: RealtimeState) => void;
  applyRealtimeEvent: (event: WSEvent) => void;
  reconcileEvents: (sessionId?: string) => Promise<void>;
  fetchOverview: (sessionId?: string) => Promise<void>;
  fetchLoop: (sessionId?: string) => Promise<void>;
  setApprovalDialogOpen: (value: boolean) => void;
  submitApproval: (approved: boolean) => Promise<void>;
};

export const useRemediationStore = create<RemediationState>((set, get) => ({
  loop: undefined,
  overview: undefined,
  events: [],
  sessionId: "",
  lastEventId: undefined,
  realtimeState: "closed",
  isLoading: false,
  approvalDialogOpen: false,
  setSessionId: (sessionId) => set({ sessionId }),
  setRealtimeState: (realtimeState) => set({ realtimeState }),
  applyRealtimeEvent: (event) => {
    const targetSessionId = String(event.session_id ?? "").trim();
    if (!targetSessionId) {
      return;
    }
    set((state) => {
      const activeSessionId = state.sessionId.trim();
      if (!activeSessionId || activeSessionId !== targetSessionId) {
        return state;
      }
      const mergedEvents = mergeSessionEvents(state.events, [event as SessionEvent]);
      if (mergedEvents.length === state.events.length) {
        return state;
      }
      return {
        events: mergedEvents,
        lastEventId: getLastEventId(mergedEvents) ?? state.lastEventId,
        overview:
          state.overview && state.overview.session_id === targetSessionId
            ? { ...state.overview, timeline: mergedEvents }
            : state.overview,
      };
    });
  },
  reconcileEvents: async (sessionId) => {
    const resolved = (sessionId ?? get().sessionId).trim();
    if (!resolved) {
      return;
    }

    const before = get();
    const after = before.lastEventId;
    let incoming: SessionEvent[] = [];
    try {
      incoming = await apiClient.getSessionEvents(resolved, 200, after);
    } catch {
      return;
    }

    if (!Array.isArray(incoming) || incoming.length === 0) {
      return;
    }

    set((state) => {
      if (state.sessionId.trim() !== resolved) {
        return state;
      }
      const mergedEvents = mergeSessionEvents(state.events, incoming);
      if (mergedEvents.length === state.events.length) {
        return state;
      }
      return {
        events: mergedEvents,
        lastEventId: getLastEventId(mergedEvents) ?? state.lastEventId,
        overview:
          state.overview && state.overview.session_id === resolved
            ? { ...state.overview, timeline: mergedEvents }
            : state.overview,
      };
    });
  },
  fetchOverview: async (sessionId) => {
    const resolved = (sessionId ?? get().sessionId).trim();
    if (!resolved) {
      return;
    }
    set({ isLoading: true });
    const overview = await apiClient.getRemediationOverview(resolved);
    const events = overview.timeline ?? [];
    set({
      overview,
      events,
      sessionId: resolved,
      lastEventId: getLastEventId(events),
      isLoading: false,
    });
  },
  fetchLoop: async (sessionId) => {
    const resolved = (sessionId ?? get().sessionId).trim();
    if (!resolved) {
      return;
    }
    set({ isLoading: true });
    const loop = await apiClient.getSessionLoop(resolved);
    const overview = await apiClient.getRemediationOverview(resolved);
    const events = overview.timeline ?? [];
    set({
      loop,
      overview,
      events,
      sessionId: resolved,
      lastEventId: getLastEventId(events),
      isLoading: false,
    });
  },
  setApprovalDialogOpen: (approvalDialogOpen) => set({ approvalDialogOpen }),
  submitApproval: async (approved: boolean) => {
    const state = get();
    const sessionId = state.sessionId || state.loop?.session_id;
    const planVersion = state.overview?.plan_version;
    if (!sessionId) {
      return;
    }
    let planKey =
      derivePlanKeyFromEvents(state.events) ??
      derivePlanKeyFromEvents(state.overview?.timeline ?? []);
    if (!planKey) {
      const session = await apiClient.getDiagnosisSession(sessionId).catch(() => null);
      planKey = getPrimaryPlanKey(session?.diagnosis_result);
    }

    let pollingStopped = false;
    let pollTimer: number | undefined;
    const pollOverview = async () => {
      if (pollingStopped) {
        return;
      }
      const nextOverview = await apiClient.getRemediationOverview(sessionId);
      const nextEvents = nextOverview.timeline ?? [];
      set({
        overview: nextOverview,
        events: nextEvents,
        lastEventId: getLastEventId(nextEvents),
      });
    };
    if (approved && typeof window !== "undefined") {
      pollTimer = window.setInterval(() => {
        void pollOverview();
      }, 2000);
      void pollOverview();
    }

    try {
      await apiClient.approveRemediation(sessionId, approved, "ui-operator", planVersion, planKey);
    } finally {
      pollingStopped = true;
      if (pollTimer !== undefined && typeof window !== "undefined") {
        window.clearInterval(pollTimer);
      }
    }
    const overview = await apiClient.getRemediationOverview(sessionId);
    const loop = await apiClient.getSessionLoop(sessionId).catch(() => undefined);
    const events = overview.timeline ?? [];
    set({
      loop: loop ?? get().loop,
      overview,
      events,
      lastEventId: getLastEventId(events),
      approvalDialogOpen: false,
    });
  },
}));
