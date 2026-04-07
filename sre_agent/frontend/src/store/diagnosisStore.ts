import { create } from "zustand";

import { apiClient } from "../api/client";
import type { ChatMessage, DiagnosisSession, DiagnosisStartedData, Observation, RemediationPlan, SessionEvent, ThinkingStep, WSEvent } from "../api/types";

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
  bootstrapSession: (sessionId?: string) => Promise<void>;
  sendMessage: (content: string) => Promise<ChatMessage | undefined>;
  revisePlan: (instruction: string) => Promise<void>;
  approvePlan: (approved: boolean) => Promise<void>;
  setConnectionState: (value: ConnectionState) => void;
  applyEvent: (event: WSEvent) => void;
};

const DEFAULT_REVISE_INSTRUCTION = "请优化当前修复方案，补充更稳妥步骤与验证";

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
    return String(data.message ?? "mock 已执行修复计划");
  }
  if (event.type === "plan_revised") {
    return `修复方案已更新为版本 ${String(data.plan_version ?? "")}`.trim();
  }
  if (event.type === "observation_started") {
    return `进入观察阶段，持续 ${String(data.seconds ?? 180)} 秒`;
  }
  if (event.type === "observation_result") {
    return `观察结果：alert_cleared=${String(data.alert_cleared ?? false)}，metrics_improved=${String(data.metrics_improved ?? false)}`;
  }
  if (event.type === "escalation_required") {
    return String(data.message ?? "需要工程师介入");
  }
  if (event.type === "remediation_progress") {
    const stage = getEventStage(event);
    if (stage === "execution_failed" || stage === "escalation_required") {
      return "需要工程师介入";
    }
    if (["execution_started", "execution_mocked", "observation_started", "observation_result"].includes(stage)) {
      return null;
    }
    if (stage === "execution_timeout") {
      return "修复执行超时退出";
    }
    if (typeof data.message === "string" && data.message.trim()) {
      return data.message;
    }
    return stage ? `修复进度：${stage}` : null;
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

function mergeSessionEvents(current: SessionEvent[], incoming: SessionEvent[]): SessionEvent[] {
  if (!incoming.length) {
    return current;
  }
  const seen = new Set<string>();
  const identity = (event: SessionEvent) => {
    const eventId = getEventDataEventId(event);
    if (eventId) {
      return `id:${eventId}`;
    }
    return `${event.type}:${event.timestamp}:${JSON.stringify(event.data ?? {})}`;
  };
  const merged: SessionEvent[] = [];
  [...current, ...incoming].forEach((event) => {
    const key = identity(event);
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
      ? `当前状态为 ${session?.status ?? "unknown"}，暂不可审批。`
      : "仅最新版本可审批，请先刷新或重新生成最新方案。";
  const planMissingReason = hasPlan ? undefined : "当前会话尚未产出修复计划，请先完成诊断或切换会话。";

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

      // Extract alert/topology from diagnosis_started events in history
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
        error: error instanceof Error ? error.message : "加载会话失败",
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
        error: error instanceof Error ? error.message : "消息发送失败",
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
            content: `修复方案已根据指令更新（${effectiveInstruction}），当前版本 v${payload.plan_version}，步骤 ${beforeSteps} -> ${afterSteps}。`,
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
        error: error instanceof Error ? error.message : "修复计划修改失败",
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
    set((state) => ({
      isApprovingPlan: true,
      error: undefined,
      session:
        approved && state.session
          ? {
              ...state.session,
              status: "remediating",
            }
          : state.session,
      messages:
        approved && state.activeSessionId
          ? [
              ...state.messages,
              {
                id: `assistant-execution-started-${Date.now()}`,
                role: "assistant",
                content: "执行修复中",
                created_at: new Date().toISOString(),
                metadata: { session_id: state.activeSessionId, event_type: "execution_started" },
              },
            ]
          : state.messages,
    }));

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
        error: error instanceof Error ? error.message : "审批操作失败",
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
      const currentTrace = state.session?.trace?.steps ?? [];
      const nextEntries = [
        toThinkingStep(event, currentTrace.length + 1),
        toObservation(event),
      ].filter((entry): entry is ThinkingStep | Observation => Boolean(entry));

      let nextSession = appendTraceEntries(state.session, nextEntries);

      // Capture alert/topology context from diagnosis_started event
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
}));
