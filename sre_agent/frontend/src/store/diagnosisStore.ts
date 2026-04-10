import { create } from "zustand";

import { apiClient, streamDiagnosis } from "../api/client";
import type { Alert, ChatMessage, DiagnosisSession, DiagnosisStartedData, Observation, RemediationPlan, SessionEvent, ThinkingStep, WSEvent } from "../api/types";

type ConnectionState = "connecting" | "open" | "closed" | "error";
type BootstrapStatus = "idle" | "loading" | "ready" | "empty" | "error";
type TraceStatus = "unknown" | "empty" | "ready";

type DiagnosisState = {
  session?: DiagnosisSession;
  activeSessionId?: string;
  messages: ChatMessage[];
  events: SessionEvent[];
  chatContextApplied: boolean;
  chatContextMeta?: Record<string, unknown>;
  isLoadingSession: boolean;
  bootstrapStatus: BootstrapStatus;
  traceStatus: TraceStatus;
  isSendingMessage: boolean;
  connectionState: ConnectionState;
  error?: string;
  isRevisingPlan: boolean;
  isApprovingPlan: boolean;
  currentPlanVersion: number | null;
  latestPlanVersion: number | null;
  approvedPlanVersion: number | null;
  canApprove: boolean;
  approvalBlockReason?: string;
  hasPlan: boolean;
  planMissingReason?: string;
  effectiveReviseInstruction?: string;
  alertSnapshot: DiagnosisStartedData["alert"] | null;
  topologyContext: DiagnosisStartedData["topology"] | null;
  // SSE streaming state
  streamingText: string;
  streamingNode: string | null;
  isStreamingDiagnosis: boolean;
  activeStreamingTools: Array<{ tool: string; params: Record<string, unknown> }>;
  streamingAbortController: AbortController | null;
  bootstrapSession: (sessionId?: string) => Promise<void>;
  sendMessage: (content: string) => Promise<ChatMessage | undefined>;
  revisePlan: (instruction: string) => Promise<void>;
  approvePlan: (approved: boolean) => Promise<void>;
  setConnectionState: (value: ConnectionState) => void;
  applyEvent: (event: WSEvent) => void;
  startStreamingDiagnosis: (alert: Alert, extraAlertFingerprints?: string[], onSessionReady?: (sessionId: string) => void) => void;
  cancelStreamingDiagnosis: () => void;
};

const DEFAULT_REVISE_INSTRUCTION = "\u8bf7\u4f18\u5316\u5f53\u524d\u4fee\u590d\u65b9\u6848\uff0c\u8865\u5145\u66f4\u7a33\u59a5\u6b65\u9aa4\u4e0e\u9a8c\u8bc1";
const SESSION_BACKFILL_THROTTLE_MS = 1200;
const sessionBackfillLastRunAt = new Map<string, number>();
const sessionBackfillInFlight = new Set<string>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toThinkingStep(event: WSEvent, fallbackStep: number): ThinkingStep | null {
  if (event.type !== "thinking_step" && event.type !== "tool_call") {
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
        : "诊断引擎正在扩展当前推理上下文。",
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
  return "诊断引擎返回了一条错误事件。";
}

type EventLike = Pick<WSEvent, "type" | "session_id" | "timestamp" | "data">;

function getEventStage(event: EventLike): string {
  if (event.type !== "remediation_progress") {
    return event.type;
  }
  const data = isRecord(event.data) ? event.data : {};
  return String(data.stage ?? "").trim().toLowerCase();
}

function formatRemediationEventMessage(event: EventLike): string | null {
  const data = isRecord(event.data) ? event.data : {};
  if (event.type === "execution_mocked") {
    return String(data.message ?? "mock \u5df2\u6267\u884c\u4fee\u590d\u8ba1\u5212");
  }
  if (event.type === "plan_revised") {
    return `\u4fee\u590d\u65b9\u6848\u5df2\u66f4\u65b0\u4e3a\u7248\u672c ${String(data.plan_version ?? "")}`.trim();
  }
  if (event.type === "observation_started") {
    return `\u8fdb\u5165\u89c2\u5bdf\u9636\u6bb5\uff0c\u6301\u7eed ${String(data.seconds ?? 180)} \u79d2`;
  }
  if (event.type === "observation_result") {
    const baselineAlert = isRecord(data.baseline_alert) ? data.baseline_alert : {};
    const postAlert = isRecord(data.post_alert) ? data.post_alert : {};
    const beforeStatus =
      typeof baselineAlert.status === "string" && baselineAlert.status.trim() ? baselineAlert.status.trim() : "unknown";
    const afterStatus =
      typeof postAlert.status === "string" && postAlert.status.trim() ? postAlert.status.trim() : "unknown";
    return `\u89c2\u5bdf\u7ed3\u679c\uff1aalert_cleared=${String(data.alert_cleared ?? false)}\uff0cmetrics_improved=${String(data.metrics_improved ?? false)}\uff0c\u544a\u8b66\u72b6\u6001 ${beforeStatus} -> ${afterStatus}`;
  }
  if (event.type === "escalation_required") {
    return String(data.message ?? "\u9700\u8981\u5de5\u7a0b\u5e08\u4ecb\u5165");
  }
  if (event.type === "remediation_progress") {
    const stage = getEventStage(event);
    if (stage === "execution_failed" || stage === "escalation_required") {
      return "\u9700\u8981\u5de5\u7a0b\u5e08\u4ecb\u5165";
    }
    if (["execution_started", "execution_mocked", "observation_started", "observation_result"].includes(stage)) {
      return null;
    }
    if (stage === "execution_timeout") {
      return "\u4fee\u590d\u6267\u884c\u8d85\u65f6\u9000\u51fa";
    }
    if (typeof data.message === "string" && data.message.trim()) {
      return data.message;
    }
    return stage ? `\u4fee\u590d\u8fdb\u5ea6\uff1a${stage}` : null;
  }
  return null;
}

function toEventMessageKey(event: EventLike): string {
  return `${event.type}:${getEventStage(event)}:${event.timestamp}`;
}

function mergeEventMessages(
  currentMessages: ChatMessage[],
  events: EventLike[],
  sessionId: string | undefined,
): ChatMessage[] {
  if (!sessionId || events.length === 0) {
    return currentMessages;
  }
  const existingKeys = new Set(
    currentMessages
      .map((message) => String(message.metadata?.event_key ?? ""))
      .filter((value) => value.length > 0),
  );
  const nextMessages = [...currentMessages];

  events.forEach((event) => {
    const content = formatRemediationEventMessage(event);
    if (!content) {
      return;
    }
    const eventKey = toEventMessageKey(event);
    if (existingKeys.has(eventKey)) {
      return;
    }
    existingKeys.add(eventKey);
    nextMessages.push({
      id: `event-${eventKey.replace(/[^a-zA-Z0-9:_-]/g, "-")}`,
      role: "assistant",
      content,
      created_at: event.timestamp,
      metadata: {
        session_id: sessionId,
        event_type: event.type,
        event_key: eventKey,
      },
    });
  });

  return nextMessages;
}

function parsePlanVersionFromPlanId(planId: string | undefined): number | null {
  if (!planId) {
    return null;
  }
  const matched = /-v(\d+)$/.exec(planId.trim());
  if (!matched) {
    return null;
  }
  const version = Number(matched[1]);
  return Number.isFinite(version) && version > 0 ? version : null;
}

function normalizePlanVersion(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function sortByCandidateRank(left: { rank?: number }, right: { rank?: number }) {
  const lhs = Number(left.rank ?? Number.POSITIVE_INFINITY);
  const rhs = Number(right.rank ?? Number.POSITIVE_INFINITY);
  return lhs - rhs;
}

export function extractRecommendedPlan(session: DiagnosisSession | undefined): RemediationPlan | null {
  if (!session?.diagnosis_result) {
    return null;
  }
  if (session.diagnosis_result.recommended_fix) {
    return session.diagnosis_result.recommended_fix;
  }
  const rankedCandidates = [...(session.diagnosis_result.ranked_candidates ?? [])].sort(sortByCandidateRank);
  for (const candidate of rankedCandidates) {
    if (candidate.recommended_fix) {
      return candidate.recommended_fix;
    }
  }
  return null;
}

function diagnosisResultHasRecommendedPlan(
  diagnosisResult: DiagnosisSession["diagnosis_result"] | null | undefined,
): boolean {
  if (!diagnosisResult) {
    return false;
  }
  if (diagnosisResult.recommended_fix) {
    return true;
  }
  const rankedCandidates = diagnosisResult.ranked_candidates ?? [];
  return rankedCandidates.some((candidate) => Boolean(candidate?.recommended_fix));
}

function getEventDataEventId(event: { data?: Record<string, unknown> }): string | undefined {
  const data = event.data;
  if (!isRecord(data)) {
    return undefined;
  }
  const rawValue = data.event_id;
  if (rawValue === undefined || rawValue === null) {
    return undefined;
  }
  const value = String(rawValue).trim();
  return value.length > 0 ? value : undefined;
}

function getEventIdentity(event: EventLike): string {
  const eventId = getEventDataEventId(event);
  if (eventId) {
    return `id:${eventId}`;
  }
  return `${event.type}:${event.timestamp}:${JSON.stringify(event.data ?? {})}`;
}

function mergeSessionEvents(current: SessionEvent[], incoming: SessionEvent[]): SessionEvent[] {
  if (!incoming.length) {
    return current;
  }
  const seen = new Set<string>();
  const merged: SessionEvent[] = [];
  [...current, ...incoming].forEach((event) => {
    const key = getEventIdentity(event);
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    merged.push(event);
  });
  return merged;
}

function getLastEventId(events: SessionEvent[]): string | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const eventId = getEventDataEventId(events[index]);
    if (eventId) {
      return eventId;
    }
  }
  return undefined;
}

function shouldTriggerSessionBackfill(event: WSEvent): boolean {
  if (event.type === "diagnosis_result" || event.type === "approval_required" || event.type === "plan_revised") {
    return true;
  }
  if (event.type !== "remediation_progress") {
    return false;
  }
  const data = isRecord(event.data) ? event.data : {};
  const stage = String(data.stage ?? "").trim().toLowerCase();
  return [
    "execution_started",
    "execution_succeeded",
    "execution_failed",
    "execution_timeout",
    "escalation_required",
    "observation_result",
  ].includes(stage);
}

async function runSessionBackfill(sessionId: string): Promise<void> {
  if (sessionBackfillInFlight.has(sessionId)) {
    return;
  }
  sessionBackfillInFlight.add(sessionId);
  try {
    const snapshot = useDiagnosisStore.getState();
    const activeSessionId = snapshot.activeSessionId ?? snapshot.session?.session_id;
    if (!activeSessionId || activeSessionId !== sessionId) {
      return;
    }
    const after = getLastEventId(snapshot.events);
    const [session, incomingEvents] = await Promise.all([
      apiClient.getDiagnosisSession(sessionId).catch(() => null),
      apiClient.getSessionEvents(sessionId, 200, after).catch(() => [] as SessionEvent[]),
    ]);
    const mergedEvents = mergeSessionEvents(snapshot.events, Array.isArray(incomingEvents) ? incomingEvents : []);
    const nextSession = session ?? snapshot.session;
    const approvalState = deriveApprovalState(nextSession ?? undefined, mergedEvents);
    const traceStatus: TraceStatus = nextSession?.trace?.steps?.length ? "ready" : "empty";
    useDiagnosisStore.setState((state) => ({
      session: nextSession ?? undefined,
      events: mergedEvents,
      traceStatus,
      messages: mergeEventMessages(state.messages, mergedEvents, sessionId),
      ...approvalState,
    }));
  } finally {
    sessionBackfillInFlight.delete(sessionId);
  }
}

function scheduleSessionBackfill(sessionId: string): void {
  const now = Date.now();
  const lastRunAt = sessionBackfillLastRunAt.get(sessionId) ?? 0;
  if (now - lastRunAt < SESSION_BACKFILL_THROTTLE_MS) {
    return;
  }
  sessionBackfillLastRunAt.set(sessionId, now);
  void runSessionBackfill(sessionId);
}

function deriveApprovalState(
  session: DiagnosisSession | undefined,
  events: SessionEvent[],
): Pick<
  DiagnosisState,
  | "currentPlanVersion"
  | "latestPlanVersion"
  | "approvedPlanVersion"
  | "canApprove"
  | "approvalBlockReason"
  | "hasPlan"
  | "planMissingReason"
> {
  const plan = extractRecommendedPlan(session);
  const hasPlan = Boolean(plan && plan.steps);
  const currentPlanVersion = parsePlanVersionFromPlanId(plan?.plan_id) ?? 1;
  let latestPlanVersion = currentPlanVersion;
  let approvedPlanVersion: number | null = null;

  events.forEach((event) => {
    const data = isRecord(event.data) ? event.data : {};
    const explicitVersion = normalizePlanVersion(data.plan_version);
    if (event.type === "plan_revised" || event.type === "approval_required") {
      if (explicitVersion && explicitVersion > latestPlanVersion) {
        latestPlanVersion = explicitVersion;
      }
      return;
    }
    if (event.type !== "remediation_progress") {
      return;
    }
    const stage = String(data.stage ?? "").trim().toLowerCase();
    if (stage === "execution_started" && explicitVersion) {
      approvedPlanVersion = explicitVersion;
      if (explicitVersion > latestPlanVersion) {
        latestPlanVersion = explicitVersion;
      }
    }
  });

  const canApprove = session?.status === "approval_required" && currentPlanVersion === latestPlanVersion;
  const approvalBlockReason = canApprove
    ? undefined
    : session?.status !== "approval_required"
      ? `\u5f53\u524d\u72b6\u6001\u4e3a ${session?.status ?? "unknown"}\uff0c\u6682\u4e0d\u53ef\u5ba1\u6279\u3002`
      : "\u4ec5\u6700\u65b0\u7248\u672c\u53ef\u5ba1\u6279\uff0c\u8bf7\u5148\u5237\u65b0\u6216\u91cd\u65b0\u751f\u6210\u6700\u65b0\u65b9\u6848\u3002";
  const planMissingReason = hasPlan ? undefined : "\u5f53\u524d\u4f1a\u8bdd\u5c1a\u672a\u4ea7\u51fa\u4fee\u590d\u8ba1\u5212\uff0c\u8bf7\u5148\u5b8c\u6210\u8bca\u65ad\u6216\u5207\u6362\u4f1a\u8bdd\u3002";

  return {
    currentPlanVersion,
    latestPlanVersion,
    approvedPlanVersion,
    canApprove,
    approvalBlockReason,
    hasPlan,
    planMissingReason,
  };
}

function getPlanStepCount(session: DiagnosisSession | undefined): number {
  return extractRecommendedPlan(session)?.steps?.length ?? 0;
}

export const useDiagnosisStore = create<DiagnosisState>((set, get) => ({
  session: undefined,
  activeSessionId: undefined,
  messages: [],
  events: [],
  chatContextApplied: false,
  chatContextMeta: undefined,
  isLoadingSession: false,
  bootstrapStatus: "idle",
  traceStatus: "unknown",
  isSendingMessage: false,
  isRevisingPlan: false,
  isApprovingPlan: false,
  currentPlanVersion: null,
  latestPlanVersion: null,
  approvedPlanVersion: null,
  canApprove: false,
  approvalBlockReason: undefined,
  hasPlan: false,
  planMissingReason: undefined,
  effectiveReviseInstruction: undefined,
  alertSnapshot: null,
  topologyContext: null,
  streamingText: "",
  streamingNode: null,
  isStreamingDiagnosis: false,
  activeStreamingTools: [],
  streamingAbortController: null,
  connectionState: "closed",
  error: undefined,
  bootstrapSession: async (sessionId) => {
    const explicitSessionId = sessionId?.trim();

    set({
      activeSessionId: explicitSessionId ?? get().activeSessionId,
      error: undefined,
      isLoadingSession: true,
      bootstrapStatus: "loading",
      traceStatus: "unknown",
      isSendingMessage: false,
      isRevisingPlan: false,
      isApprovingPlan: false,
      messages: [],
      events: [],
      currentPlanVersion: null,
      latestPlanVersion: null,
      approvedPlanVersion: null,
      canApprove: false,
      approvalBlockReason: undefined,
      hasPlan: false,
      planMissingReason: undefined,
      effectiveReviseInstruction: undefined,
      chatContextApplied: false,
      chatContextMeta: undefined,
      alertSnapshot: null,
      topologyContext: null,
    });

    try {
      let session: DiagnosisSession | null = null;
      let resolvedSessionId = explicitSessionId;

      if (!resolvedSessionId) {
        session = await apiClient.getDiagnosisSession();
        resolvedSessionId = session?.session_id;
      } else {
        for (let attempt = 0; attempt < 10; attempt += 1) {
          try {
            const currentSession = await apiClient.getDiagnosisSession(resolvedSessionId);
            if (currentSession && currentSession.session_id === resolvedSessionId) {
              session = currentSession;
              break;
            }
          } catch (error) {
            if (attempt === 9) {
              throw error;
            }
          }
          await new Promise((resolve) => {
            globalThis.setTimeout(resolve, 1200);
          });
        }
      }

      if (!session || !resolvedSessionId) {
        set({
          session: undefined,
          activeSessionId: undefined,
          messages: [],
          events: [],
          currentPlanVersion: null,
          latestPlanVersion: null,
          approvedPlanVersion: null,
          canApprove: false,
          approvalBlockReason: undefined,
          hasPlan: false,
          planMissingReason: undefined,
          effectiveReviseInstruction: undefined,
          chatContextApplied: false,
          chatContextMeta: undefined,
          isLoadingSession: false,
          bootstrapStatus: "empty",
          traceStatus: "unknown",
          isSendingMessage: false,
          isRevisingPlan: false,
          isApprovingPlan: false,
          error: undefined,
        });
        return;
      }

      const messages = await apiClient.getChatHistory(resolvedSessionId);
      const events = await apiClient.getSessionEvents(resolvedSessionId).catch(() => []);
      const traceSteps = session.trace?.steps ?? [];
      const traceStatus: TraceStatus = traceSteps.length ? "ready" : "empty";
      const approvalState = deriveApprovalState(session, events);

      const startedEvent = Array.isArray(events)
        ? events.find((e) => e.type === "diagnosis_started")
        : undefined;
      const startedData = startedEvent && isRecord(startedEvent.data) ? startedEvent.data : {};
      const historicalAlert = (startedData.alert as DiagnosisStartedData["alert"]) ?? null;
      const historicalTopology = (startedData.topology as DiagnosisStartedData["topology"]) ?? null;

      set({
        session,
        activeSessionId: resolvedSessionId,
        messages: mergeEventMessages(messages, events, resolvedSessionId),
        events,
        isLoadingSession: false,
        bootstrapStatus: "ready",
        traceStatus,
        isSendingMessage: false,
        isRevisingPlan: false,
        isApprovingPlan: false,
        ...approvalState,
        effectiveReviseInstruction: undefined,
        chatContextApplied: false,
        chatContextMeta: undefined,
        alertSnapshot: historicalAlert,
        topologyContext: historicalTopology,
        error: undefined,
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "\u52a0\u8f7d\u4f1a\u8bdd\u5931\u8d25",
        isLoadingSession: false,
        bootstrapStatus: "error",
        traceStatus: "unknown",
        session: undefined,
        activeSessionId: explicitSessionId,
        messages: [],
        events: [],
        currentPlanVersion: null,
        latestPlanVersion: null,
        approvedPlanVersion: null,
        canApprove: false,
        approvalBlockReason: undefined,
        hasPlan: false,
        planMissingReason: undefined,
        effectiveReviseInstruction: undefined,
        chatContextApplied: false,
        chatContextMeta: undefined,
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
      const chatMeta = (reply.metadata?.["chat_meta"] ?? null) as Record<string, unknown> | null;
      const contextApplied = chatMeta?.context_applied === true;
      set((state) => ({
        messages: [...state.messages, reply],
        isSendingMessage: false,
        chatContextApplied: contextApplied,
        chatContextMeta: chatMeta ?? undefined,
      }));
      return reply;
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "\u6d88\u606f\u53d1\u9001\u5931\u8d25",
        isSendingMessage: false,
      });
      throw error;
    }
  },
  revisePlan: async (instruction: string) => {
    const sessionId = get().activeSessionId;
    const basePlanVersion = get().latestPlanVersion ?? undefined;
    const beforeSteps = getPlanStepCount(get().session);
    const text = instruction.trim();
    const effectiveInstruction = text || DEFAULT_REVISE_INSTRUCTION;
    if (!sessionId) {
      return;
    }
    set({ isRevisingPlan: true, error: undefined, effectiveReviseInstruction: effectiveInstruction });
    try {
      const payload = await apiClient.reviseRemediationPlan(sessionId, effectiveInstruction, basePlanVersion);
      const events = await apiClient.getSessionEvents(sessionId).catch(() => []);
      const approvalState = deriveApprovalState(payload.session, events);
      const afterSteps = getPlanStepCount(payload.session);
      set((state) => ({
        session: payload.session,
        events,
        messages: [
          ...state.messages,
          {
            id: `assistant-plan-revised-${Date.now()}`,
            role: "assistant",
            content: `\u4fee\u590d\u65b9\u6848\u5df2\u6839\u636e\u6307\u4ee4\u66f4\u65b0\uff08${effectiveInstruction}\uff09\uff0c\u5f53\u524d\u7248\u672c v${payload.plan_version}\uff0c\u6b65\u9aa4 ${beforeSteps} -> ${afterSteps}\u3002`,
            created_at: new Date().toISOString(),
          },
        ],
        ...approvalState,
        effectiveReviseInstruction: effectiveInstruction,
        isRevisingPlan: false,
      }));
    } catch (error) {
      set({
        isRevisingPlan: false,
        error: error instanceof Error ? error.message : "\u4fee\u590d\u8ba1\u5212\u4fee\u6539\u5931\u8d25",
      });
      throw error;
    }
  },
  approvePlan: async (approved: boolean) => {
    const sessionId = get().activeSessionId;
    const planVersion = get().latestPlanVersion ?? undefined;
    if (!sessionId) {
      return;
    }
    set({
      isApprovingPlan: true,
      error: undefined,
    });

    let pollingStopped = false;
    let pollTimer: number | undefined;
    const pollStatus = async () => {
      if (!sessionId || pollingStopped) {
        return;
      }
      const [session, events] = await Promise.all([
        apiClient.getDiagnosisSession(sessionId),
        apiClient.getSessionEvents(sessionId).catch(() => []),
      ]);
      const approvalState = deriveApprovalState(session ?? undefined, events);
      set((state) => ({
        session: session ?? undefined,
        events,
        ...approvalState,
        messages: mergeEventMessages(state.messages, events, sessionId),
      }));
    };

    if (approved && typeof window !== "undefined") {
      pollTimer = window.setInterval(() => {
        void pollStatus();
      }, 2000);
      void pollStatus();
    }

    try {
      await apiClient.approveRemediation(sessionId, approved, "ui-operator", planVersion);
    } catch (error) {
      pollingStopped = true;
      if (pollTimer !== undefined && typeof window !== "undefined") {
        window.clearInterval(pollTimer);
      }
      set({
        isApprovingPlan: false,
        error: error instanceof Error ? error.message : "\u5ba1\u6279\u64cd\u4f5c\u5931\u8d25",
      });
      throw error;
    }

    pollingStopped = true;
    if (pollTimer !== undefined && typeof window !== "undefined") {
      window.clearInterval(pollTimer);
    }
    const session = await apiClient.getDiagnosisSession(sessionId);
    const events = await apiClient.getSessionEvents(sessionId).catch(() => []);
    const approvalState = deriveApprovalState(session ?? undefined, events);
    set((state) => ({
      session: session ?? undefined,
      events,
      ...approvalState,
      isApprovingPlan: false,
      messages: mergeEventMessages(state.messages, events, sessionId),
    }));
  },
  setConnectionState: (connectionState) => set({ connectionState }),
  applyEvent: (event) => {
    const currentState = get();
    const activeSessionId = currentState.activeSessionId ?? currentState.session?.session_id;
    const targetSessionId = activeSessionId ?? event.session_id;
    if (activeSessionId && event.session_id !== activeSessionId) {
      return;
    }
    set((state) => {
      const eventIdentity = getEventIdentity(event);
      const isDuplicateEvent = state.events.some((existingEvent) => getEventIdentity(existingEvent) === eventIdentity);
      if (isDuplicateEvent) {
        return state;
      }

      const currentTrace = state.session?.trace?.steps ?? [];
      const nextEntries = [
        toThinkingStep(event, currentTrace.length + 1),
        toObservation(event),
      ].filter((entry): entry is ThinkingStep | Observation => Boolean(entry));

      let nextSession = appendTraceEntries(state.session, nextEntries);

      let nextAlertSnapshot = state.alertSnapshot;
      let nextTopologyContext = state.topologyContext;
      if (event.type === "diagnosis_started") {
        const data = isRecord(event.data) ? event.data : {};
        nextAlertSnapshot = (data.alert as DiagnosisStartedData["alert"]) ?? null;
        nextTopologyContext = (data.topology as DiagnosisStartedData["topology"]) ?? null;
      }

      if (event.type === "diagnosis_result" && nextSession) {
        const diagnosisResult = event.data as DiagnosisSession["diagnosis_result"];
        nextSession = {
          ...nextSession,
          diagnosis_result: diagnosisResult,
          status: diagnosisResultHasRecommendedPlan(diagnosisResult)
            ? "approval_required"
            : nextSession.status === "diagnosing"
              ? "diagnosed"
              : nextSession.status,
        };
      }
      if (event.type === "approval_required" && nextSession) {
        nextSession = {
          ...nextSession,
          status: "approval_required",
        };
      }
      if (event.type === "plan_revised" && nextSession) {
        nextSession = {
          ...nextSession,
          status: "approval_required",
        };
      }
      if (event.type === "escalation_required" && nextSession) {
        nextSession = {
          ...nextSession,
          status: "escalated",
        };
      }
      if (event.type === "observation_result" && nextSession) {
        const data = isRecord(event.data) ? event.data : {};
        if (data.metrics_improved === true && data.alert_cleared === true) {
          nextSession = {
            ...nextSession,
            status: "resolved",
          };
        }
      }
      if (event.type === "remediation_progress" && nextSession) {
        const data = isRecord(event.data) ? event.data : {};
        const stage = String(data.stage ?? "").trim().toLowerCase();
        if (stage === "execution_started") {
          nextSession = {
            ...nextSession,
            status: "remediating",
          };
        }
        if (stage === "execution_succeeded") {
          nextSession = {
            ...nextSession,
            status: "resolved",
          };
        }
        if (stage === "execution_failed") {
          nextSession = {
            ...nextSession,
            status: "failed",
          };
        }
        if (stage === "execution_timeout") {
          nextSession = {
            ...nextSession,
            status: "timeout",
          };
        }
        if (stage === "escalation_required") {
          nextSession = {
            ...nextSession,
            status: "escalated",
          };
        }
      }

      const nextEvents = mergeSessionEvents(state.events, [event as SessionEvent]);
      const approvalState = deriveApprovalState(nextSession, nextEvents);
      const nextMessages = mergeEventMessages(state.messages, [event], targetSessionId);

      return {
        session: nextSession,
        events: nextEvents,
        ...approvalState,
        effectiveReviseInstruction: state.effectiveReviseInstruction,
        messages: nextMessages,
        alertSnapshot: nextAlertSnapshot,
        topologyContext: nextTopologyContext,
        error: getEventError(event) ?? state.error,
        traceStatus:
          nextEntries.length > 0 || event.type === "diagnosis_result"
            ? "ready"
            : state.traceStatus,
      };
    });
    if (targetSessionId && shouldTriggerSessionBackfill(event)) {
      scheduleSessionBackfill(targetSessionId);
    }
  },
  startStreamingDiagnosis: (alert: Alert, extraAlertFingerprints: string[] = [], onSessionReady?: (sessionId: string) => void) => {
    const controller = new AbortController();
    set({
      streamingText: "",
      streamingNode: null,
      isStreamingDiagnosis: true,
      activeStreamingTools: [],
      streamingAbortController: controller,
      error: undefined,
    });

    // Run SSE stream in background — do NOT await so caller can navigate immediately.
    const run = async () => {
      let sessionFired = false;
      try {
        await streamDiagnosis(
          alert,
          extraAlertFingerprints,
          (event) => {
            const { type, session_id, data } = event;
            if (!sessionFired && session_id) {
              sessionFired = true;
              set({ activeSessionId: session_id });
              onSessionReady?.(session_id);
            }
            switch (type) {
              case "token_delta":
                set((state) => ({
                  streamingText: state.streamingText + String(data.content ?? ""),
                }));
                break;
              case "node_started":
                set({ streamingNode: String(data.node ?? "") });
                break;
              case "node_completed":
                set((state) => {
                  const nextSession = state.session
                    ? { ...state.session }
                    : undefined;
                  if (nextSession && data.diagnosis_result) {
                    nextSession.diagnosis_result = data.diagnosis_result as DiagnosisSession["diagnosis_result"];
                  }
                  return {
                    streamingText: "",
                    streamingNode: null,
                    session: nextSession,
                  };
                });
                break;
              case "tool_started":
                set((state) => ({
                  activeStreamingTools: [
                    ...state.activeStreamingTools,
                    { tool: String(data.tool ?? ""), params: (data.params as Record<string, unknown>) ?? {} },
                  ],
                }));
                break;
              case "tool_completed":
                set((state) => ({
                  activeStreamingTools: state.activeStreamingTools.filter(
                    (t) => t.tool !== String(data.tool ?? ""),
                  ),
                }));
                break;
              case "diagnosis_started":
                set({
                  alertSnapshot: (data.alert as DiagnosisStartedData["alert"]) ?? null,
                  topologyContext: (data.topology as DiagnosisStartedData["topology"]) ?? null,
                });
                break;
              case "done":
                set({
                  isStreamingDiagnosis: false,
                  streamingNode: null,
                  activeStreamingTools: [],
                  streamingAbortController: null,
                });
                break;
              case "error":
                set({
                  isStreamingDiagnosis: false,
                  streamingAbortController: null,
                  error: String(data.message ?? "streaming diagnosis error"),
                });
                break;
            }
          },
          controller.signal,
        );
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          set({
            isStreamingDiagnosis: false,
            streamingAbortController: null,
            error: err instanceof Error ? err.message : "streaming diagnosis failed",
          });
        }
      }
    };
    void run();
  },
  cancelStreamingDiagnosis: () => {
    const controller = get().streamingAbortController;
    controller?.abort();
    set({
      isStreamingDiagnosis: false,
      streamingText: "",
      streamingNode: null,
      activeStreamingTools: [],
      streamingAbortController: null,
    });
  },
}));
