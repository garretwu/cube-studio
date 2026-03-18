import { create } from "zustand";

import { apiClient } from "../api/client";
import type { RemediationOverview } from "../api/types";

type RemediationState = {
  overview?: RemediationOverview;
  isLoading: boolean;
  approvalDialogOpen: boolean;
  fetchOverview: () => Promise<void>;
  setApprovalDialogOpen: (value: boolean) => void;
  submitApproval: (approved: boolean) => Promise<void>;
};

export const useRemediationStore = create<RemediationState>((set, get) => ({
  overview: undefined,
  isLoading: false,
  approvalDialogOpen: false,
  fetchOverview: async () => {
    set({ isLoading: true });
    const overview = await apiClient.getRemediationOverview();
    set({ overview, isLoading: false });
  },
  setApprovalDialogOpen: (approvalDialogOpen) => set({ approvalDialogOpen }),
  submitApproval: async (approved: boolean) => {
    const sessionId = get().overview?.session_id;
    if (!sessionId) {
      return;
    }
    await apiClient.approveRemediation(sessionId, approved);
    set((state) => ({
      approvalDialogOpen: false,
      overview: state.overview
        ? { ...state.overview, approval_required: false, progress: { ...state.overview.progress, status: approved ? "approved" : "rejected" } }
        : state.overview,
    }));
  },
}));
