import { create } from "zustand";

import { apiClient } from "../api/client";
import { initialChatMessages } from "../mocks/data";
import type { ChatMessage, DiagnosisSession, Observation, ThinkingStep, WSEvent } from "../api/types";

type ConnectionState = "connecting" | "open" | "closed" | "error";

type DiagnosisState = {
  session?: DiagnosisSession;
  activeSessionId?: string;
  messages: ChatMessage[];
  isLoadingSession: boolean;
  isSendingMessage: boolean;
  connectionState: ConnectionState;
  error?: string;
  bootstrapSession: (sessionId?: string) => Promise<void>;
  sendMessage: (content: string) => Promise<ChatMessage | undefined>;
  setConnectionState: (value: ConnectionState) => void;
  applyEvent: (event: WSEvent) => void;
};

const seedMessages = () => [...initialChatMessages];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toThinkingStep(event: WSEvent, fallbackStep: number): ThinkingStep | null {
  if (event.type !== "thinking_step") {
    return null;
  }

  const data = isRecord(event.data) ? event.data : {};
  const actionType = data.action_type;

  return {
    step: typeof data.step === "number" ? data.step : fallbackStep,
    timestamp: event.timestamp,
    thought:
      typeof data.thought === "string" && data.thought.trim()
        ? data.thought
        : "The diagnosis engine is expanding the current reasoning context.",
    action_type:
      actionType === "tool_call" || actionType === "remediate" || actionType === "conclude" ? actionType : "conclude",
    tool_name: typeof data.tool_name === "string" ? data.tool_name : null,
    tool_params: isRecord(data.tool_params) ? data.tool_params : null,
    confidence: typeof data.confidence === "number" ? data.confidence : null,
  };
}

function toObservation(event: WSEvent): Observation | null {
  if (event.type !== "tool_result") {
    return null;
  }

  const data = isRecord(event.data) ? event.data : {};
  const result = isRecord(data.result) ? data.result : data;

  return {
    tool:
      typeof data.tool_name === "string"
        ? data.tool_name
        : typeof data.tool === "string"
          ? data.tool
          : "tool_result",
    params: isRecord(data.params) ? data.params : {},
    result,
    timestamp: event.timestamp,
  };
}

function appendTraceEntries(
  session: DiagnosisSession | undefined,
  entries: Array<ThinkingStep | Observation>,
): DiagnosisSession | undefined {
  if (!session || !entries.length) {
    return session;
  }

  const trace = session.trace?.steps ?? [];
  return {
    ...session,
    trace: {
      steps: [...trace, ...entries],
    },
  };
}

function getEventError(event: WSEvent) {
  if (event.type !== "error") {
    return undefined;
  }

  const data = isRecord(event.data) ? event.data : {};
  if (typeof data.message === "string" && data.message.trim()) {
    return data.message;
  }
  if (typeof data.error === "string" && data.error.trim()) {
    return data.error;
  }
  return "The diagnosis engine returned an error event.";
}

export const useDiagnosisStore = create<DiagnosisState>((set, get) => ({
  session: undefined,
  activeSessionId: undefined,
  messages: seedMessages(),
  isLoadingSession: false,
  isSendingMessage: false,
  connectionState: "closed",
  error: undefined,
  bootstrapSession: async (sessionId) => {
    const explicitSessionId = sessionId?.trim();

    set({
      activeSessionId: explicitSessionId ?? get().activeSessionId,
      error: undefined,
      isLoadingSession: true,
      isSendingMessage: false,
    });

    try {
      let session: DiagnosisSession | undefined;
      let resolvedSessionId = explicitSessionId;

      if (!resolvedSessionId) {
        session = await apiClient.getDiagnosisSession();
        resolvedSessionId = session.session_id;
      } else {
        try {
          const currentSession = await apiClient.getDiagnosisSession(resolvedSessionId);
          if (currentSession.session_id === resolvedSessionId) {
            session = currentSession;
          }
        } catch {
          session = undefined;
        }
      }

      const messages = resolvedSessionId ? await apiClient.getChatHistory(resolvedSessionId) : seedMessages();

      set({
        session,
        activeSessionId: resolvedSessionId,
        messages: messages.length ? messages : seedMessages(),
        isLoadingSession: false,
        isSendingMessage: false,
        error: undefined,
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "加载会话失败",
        isLoadingSession: false,
        session: undefined,
        messages: explicitSessionId ? [] : seedMessages(),
      });
    }
  },
  sendMessage: async (content: string) => {
    const message = content.trim();
    const sessionId = get().activeSessionId;

    if (!message || !sessionId) {
      return undefined;
    }

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: message,
      created_at: new Date().toISOString(),
      metadata: {
        session_id: sessionId,
      },
    };

    set((state) => ({
      messages: [...state.messages, userMessage],
      isSendingMessage: true,
      error: undefined,
    }));

    try {
      const reply = await apiClient.postChatMessage(sessionId, message);
      set((state) => ({
        messages: [...state.messages, reply],
        isSendingMessage: false,
      }));
      return reply;
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "消息发送失败",
        isSendingMessage: false,
      });
      throw error;
    }
  },
  setConnectionState: (connectionState) => set({ connectionState }),
  applyEvent: (event) =>
    set((state) => {
      const activeSessionId = state.activeSessionId ?? state.session?.session_id;
      if (activeSessionId && event.session_id !== activeSessionId) {
        return state;
      }

      const currentTrace = state.session?.trace?.steps ?? [];
      const nextEntries = [
        toThinkingStep(event, currentTrace.length + 1),
        toObservation(event),
      ].filter((entry): entry is ThinkingStep | Observation => Boolean(entry));

      let nextSession = appendTraceEntries(state.session, nextEntries);

      if (event.type === "diagnosis_result" && nextSession) {
        nextSession = {
          ...nextSession,
          diagnosis_result: event.data as DiagnosisSession["diagnosis_result"],
        };
      }

      return {
        session: nextSession,
        messages: state.messages,
        error: getEventError(event) ?? state.error,
      };
    }),
}));
