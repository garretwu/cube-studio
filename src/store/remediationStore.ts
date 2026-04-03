import { create } from "zustand";

import { apiClient } from "../api/client";
import type { LoopResult, RemediationOverview, SessionEvent } from "../api/types";

type RemediationState = {
  loop?: LoopResult;
  overview?: RemediationOverview;
  events: SessionEvent[];
  sessionId: string;
  isLoading: boolean;
  approvalDialogOpen: boolean;
  setSessionId: (sessionId: string) => void;
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
  isLoading: false,
  approvalDialogOpen: false,
  setSessionId: (sessionId) => set({ sessionId }),
  fetchOverview: async (sessionId) => {
    const resolved = (sessionId ?? get().sessionId).trim();
    if (!resolved) {
      return;
    }
    set({ isLoading: true });
    const overview = await apiClient.getRemediationOverview(resolved);
    set({ overview, events: overview.timeline ?? [], sessionId: resolved, isLoading: false });
  },
  fetchLoop: async (sessionId) => {
    const resolved = (sessionId ?? get().sessionId).trim();
    if (!resolved) {
      return;
    }
    set({ isLoading: true });
    const loop = await apiClient.getSessionLoop(resolved);
    const overview = await apiClient.getRemediationOverview(resolved);
    set({ loop, overview, events: overview.timeline ?? [], sessionId: resolved, isLoading: false });
  },
  setApprovalDialogOpen: (approvalDialogOpen) => set({ approvalDialogOpen }),
  submitApproval: async (approved: boolean) => {
    const sessionId = get().sessionId || get().loop?.session_id;
    if (!sessionId) {
      return;
    }
    set((state) => ({
      approvalDialogOpen: false,
      overview:
        approved && state.overview
          ? {
              ...state.overview,
              progress: {
                ...state.overview.progress,
                status: "remediating",
              },
            }
          : state.overview,
    }));

    let pollingStopped = false;
    let pollTimer: number | undefined;
    const pollOverview = async () => {
      if (pollingStopped) {
        return;
      }
      const nextOverview = await apiClient.getRemediationOverview(sessionId);
      set({
        overview: nextOverview,
        events: nextOverview.timeline ?? [],
      });
    };
    if (approved && typeof window !== "undefined") {
      pollTimer = window.setInterval(() => {
        void pollOverview();
      }, 2000);
      void pollOverview();
    }

    try {
      await apiClient.approveRemediation(sessionId, approved);
    } finally {
      pollingStopped = true;
      if (pollTimer !== undefined && typeof window !== "undefined") {
        window.clearInterval(pollTimer);
      }
    }
    const loop = await apiClient.getSessionLoop(sessionId);
    const overview = await apiClient.getRemediationOverview(sessionId);
    set({
      loop,
      overview,
      events: overview.timeline ?? [],
    });
  },
}));
