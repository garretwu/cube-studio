import { create } from "zustand";

import { apiClient } from "../api/client";
import type { DiagnosisSession, Observation, ThinkingStep, WSEvent } from "../api/types";

type DiagnosisState = {
  session?: DiagnosisSession;
  sessionId: string;
  connectionState: "connecting" | "open" | "closed" | "error";
  setSessionId: (sessionId: string) => void;
  fetchSession: (sessionId?: string) => Promise<void>;
  setConnectionState: (value: DiagnosisState["connectionState"]) => void;
  applyEvent: (event: WSEvent) => void;
};

export const useDiagnosisStore = create<DiagnosisState>((set) => ({
  session: undefined,
  sessionId: "",
  connectionState: "closed",
  setSessionId: (sessionId) => set({ sessionId }),
  fetchSession: async (sessionId) => {
    const resolved = (sessionId ?? "").trim();
    if (!resolved) {
      return;
    }
    const session = await apiClient.getDiagnosisSession(resolved);
    set({ session, sessionId: resolved });
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
