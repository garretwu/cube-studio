import { create } from "zustand";

import { apiClient } from "../api/client";
import type { DiagnosisSession, Observation, ThinkingStep, WSEvent } from "../api/types";

type DiagnosisState = {
  session?: DiagnosisSession;
  sessionId: string;
  connectionState: "connecting" | "open" | "closed" | "error";
  seenEventIds: Record<string, boolean>;
  setSessionId: (sessionId: string) => void;
  fetchSession: (sessionId?: string) => Promise<void>;
  setConnectionState: (value: DiagnosisState["connectionState"]) => void;
  applyEvent: (event: WSEvent) => void;
};

function toRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object") {
    return value as Record<string, unknown>;
  }
  return {};
}

function toNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

function buildThinkingStep(
  event: WSEvent,
  payload: Record<string, unknown>,
  fallbackStep: number,
): ThinkingStep {
  const stepValue = toNumber(payload.step) ?? fallbackStep;
  const actionType =
    event.type === "tool_call"
      ? "tool_call"
      : payload.action_type === "remediate"
        ? "remediate"
        : payload.action_type === "conclude"
          ? "conclude"
          : "tool_call";
  const toolName =
    typeof payload.tool_name === "string"
      ? payload.tool_name
      : typeof payload.tool === "string"
        ? payload.tool
        : null;
  const toolParams = toRecord(payload.tool_params ?? payload.params);
  const thoughtValue =
    typeof payload.thought === "string" && payload.thought.trim()
      ? payload.thought
      : typeof payload.content === "string" && payload.content.trim()
        ? payload.content
        : toolName
          ? `调用工具 ${toolName}`
          : "诊断推理步骤";
  return {
    step: stepValue,
    timestamp: typeof payload.timestamp === "string" ? payload.timestamp : event.timestamp,
    thought: thoughtValue,
    action_type: actionType,
    stage: typeof payload.stage === "string" ? payload.stage : null,
    tool_name: toolName,
    tool_params: Object.keys(toolParams).length > 0 ? toolParams : null,
    confidence: toNumber(payload.confidence) ?? null,
  };
}

function buildObservation(event: WSEvent, payload: Record<string, unknown>): Observation {
  return {
    tool: typeof payload.tool === "string" ? payload.tool : "unknown",
    params: toRecord(payload.params),
    result: toRecord(payload.result),
    timestamp: typeof payload.timestamp === "string" ? payload.timestamp : event.timestamp,
  };
}

function extractEventId(event: WSEvent): string | null {
  const payload = toRecord(event.data);
  const candidate = payload.event_id;
  if (typeof candidate === "string" && candidate.trim()) {
    return candidate;
  }
  if (typeof candidate === "number" && Number.isFinite(candidate)) {
    return String(candidate);
  }
  return null;
}

const remediationStageCopy: Record<string, string> = {
  approval_rejected: "审批被拒绝",
  execution_failed: "修复执行失败",
  execution_started: "修复执行中",
  execution_succeeded: "修复执行完成",
  rollback_failed: "回滚失败",
  rollback_started: "回滚进行中",
  rollback_succeeded: "回滚完成",
};

function buildRemediationProgressThought(payload: Record<string, unknown>): string {
  const stage = typeof payload.stage === "string" ? payload.stage : "remediation_progress";
  const stageText = remediationStageCopy[stage] ?? stage;
  if (typeof payload.error === "string" && payload.error.trim()) {
    return `${stageText}: ${payload.error}`;
  }
  if (typeof payload.reason === "string" && payload.reason.trim()) {
    return `${stageText}: ${payload.reason}`;
  }
  return stageText;
}

export const useDiagnosisStore = create<DiagnosisState>((set) => ({
  session: undefined,
  sessionId: "",
  connectionState: "closed",
  seenEventIds: {},
  setSessionId: (sessionId) => set({ sessionId }),
  fetchSession: async (sessionId) => {
    const resolved = (sessionId ?? "").trim();
    if (!resolved) {
      return;
    }
    set({ session: undefined, sessionId: resolved, seenEventIds: {} });
    const session = await apiClient.getDiagnosisSession(resolved);
    set({ session, sessionId: resolved, seenEventIds: {} });
  },
  setConnectionState: (connectionState) => set({ connectionState }),
  applyEvent: (event) =>
    set((state) => {
      if (!state.session) {
        return state;
      }
      if (event.session_id && event.session_id !== state.session.session_id) {
        return state;
      }
      const eventId = extractEventId(event);
      if (eventId && state.seenEventIds[eventId]) {
        return state;
      }
      const nextSeen = eventId ? { ...state.seenEventIds, [eventId]: true } : state.seenEventIds;
      const trace = state.session.trace?.steps ?? [];
      if (event.type === "tool_call" || event.type === "thinking_step") {
        const thoughtCount = trace.reduce((count, item) => ("step" in item ? count + 1 : count), 0);
        const nextStep = buildThinkingStep(event, toRecord(event.data), thoughtCount + 1);
        return {
          seenEventIds: nextSeen,
          session: {
            ...state.session,
            trace: { steps: [...trace, nextStep] },
          },
        };
      }
      if (event.type === "tool_result") {
        const observation = buildObservation(event, toRecord(event.data));
        return {
          seenEventIds: nextSeen,
          session: {
            ...state.session,
            trace: { steps: [...trace, observation] },
          },
        };
      }
      if (event.type === "diagnosis_result") {
        return {
          seenEventIds: nextSeen,
          session: {
            ...state.session,
            diagnosis_result: event.data as DiagnosisSession["diagnosis_result"],
            status: "diagnosed",
          },
        };
      }
      if (event.type === "remediation_progress") {
        const payload = toRecord(event.data);
        const thoughtCount = trace.reduce((count, item) => ("step" in item ? count + 1 : count), 0);
        const nextStep: ThinkingStep = {
          step: thoughtCount + 1,
          timestamp: typeof payload.timestamp === "string" ? payload.timestamp : event.timestamp,
          thought: buildRemediationProgressThought(payload),
          action_type: "remediate",
          stage: typeof payload.stage === "string" ? payload.stage : null,
          tool_name: "remediation",
          tool_params: Object.keys(payload).length > 0 ? payload : null,
          confidence: null,
        };
        return {
          seenEventIds: nextSeen,
          session: {
            ...state.session,
            trace: { steps: [...trace, nextStep] },
          },
        };
      }
      if (event.type === "done") {
        const payload = toRecord(event.data);
        const outcome = typeof payload.outcome === "string" ? payload.outcome : null;
        return {
          seenEventIds: nextSeen,
          session: {
            ...state.session,
            outcome,
            status: "resolved",
          },
        };
      }
      if (event.type === "error") {
        return {
          seenEventIds: nextSeen,
          session: {
            ...state.session,
            status: "failed",
          },
        };
      }
      if (eventId) {
        return { seenEventIds: nextSeen };
      }
      return state;
    }),
}));
