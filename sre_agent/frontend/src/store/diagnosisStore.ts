import { create } from "zustand";

import { apiClient } from "../api/client";
import type { DiagnosisSession, Observation, ThinkingStep, WSEvent } from "../api/types";

type DiagnosisState = {
  session?: DiagnosisSession;
  connectionState: "connecting" | "open" | "closed" | "error";
  fetchSession: () => Promise<void>;
  setConnectionState: (value: DiagnosisState["connectionState"]) => void;
  applyEvent: (event: WSEvent) => void;
};

export const useDiagnosisStore = create<DiagnosisState>((set) => ({
  session: undefined,
  connectionState: "closed",
  fetchSession: async () => {
    const session = await apiClient.getDiagnosisSession();
    set({ session });
  },
  setConnectionState: (connectionState) => set({ connectionState }),
  applyEvent: (event) =>
    set((state) => {
      if (!state.session) {
        return state;
      }
      if (event.type === "thinking_step") {
        const trace = state.session.trace?.steps ?? [];
        const nextStep = event.data as unknown as ThinkingStep | Observation;
        return {
          session: {
            ...state.session,
            trace: { steps: [...trace, nextStep] },
          },
        };
      }
      return state;
    }),
}));
