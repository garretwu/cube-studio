import { create } from "zustand";

import { apiClient } from "../api/client";
import type { LoopResult } from "../api/types";

type RemediationState = {
  loop?: LoopResult;
  sessionId: string;
  isLoading: boolean;
  approvalDialogOpen: boolean;
  setSessionId: (sessionId: string) => void;
  fetchLoop: (sessionId?: string) => Promise<void>;
  setApprovalDialogOpen: (value: boolean) => void;
  submitApproval: (approved: boolean) => Promise<void>;
};

export const useRemediationStore = create<RemediationState>((set, get) => ({
  loop: undefined,
  sessionId: "",
  isLoading: false,
  approvalDialogOpen: false,
  setSessionId: (sessionId) => set({ sessionId }),
  fetchLoop: async (sessionId) => {
    const resolved = (sessionId ?? get().sessionId).trim();
    if (!resolved) {
      return;
    }
    set({ isLoading: true });
    const loop = await apiClient.getSessionLoop(resolved);
    set({ loop, sessionId: resolved, isLoading: false });
  },
  setApprovalDialogOpen: (approvalDialogOpen) => set({ approvalDialogOpen }),
  submitApproval: async (approved: boolean) => {
    const sessionId = get().sessionId || get().loop?.session_id;
    if (!sessionId) {
      return;
    }
    await apiClient.approveRemediation(sessionId, approved);
    const loop = await apiClient.getSessionLoop(sessionId);
    set({
      approvalDialogOpen: false,
      loop,
    });
  },
}));
