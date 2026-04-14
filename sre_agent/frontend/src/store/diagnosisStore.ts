import { create } from "zustand";

import { apiClient, streamDiagnosis } from "../api/client";
import type {
  Alert,
  ChatMessage,
  DiagnosisLocalAuditRecord,
  DiagnosisSession,
  DiagnosisStartedData,
  LiveFinalAnswerBlock,
  LiveThinkingBlock,
  Observation,
  RemediationPlan,
  SessionEvent,
  StreamingToolCall,
  ThinkingStep,
  WSEvent,
} from "../api/types";
import { formatDateTime } from "../utils/format";

type ConnectionState = "connecting" | "open" | "closed" | "error";
type BootstrapStatus = "idle" | "loading" | "ready" | "empty" | "error";
type TraceStatus = "unknown" | "empty" | "ready";

export type ApprovalDecisionInput = {
  approved: boolean;
  reason?: string;
  user?: string;
};

type DiagnosisState = {
  session?: DiagnosisSession;
  activeSessionId?: string;
  messages: ChatMessage[];
  events: SessionEvent[];
  localAuditRecords: DiagnosisLocalAuditRecord[];
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
  approvalOverlayOpen: boolean;
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
  liveThinking: LiveThinkingBlock | null;
  liveFinalAnswer: LiveFinalAnswerBlock | null;
  streamingText: string;
  streamingNode: string | null;
  isStreamingDiagnosis: boolean;
  activeStreamingTools: StreamingToolCall[];
  streamingAbortController: AbortController | null;
  bootstrapSession: (sessionId?: string) => Promise<void>;
  sendMessage: (content: string) => Promise<ChatMessage | undefined>;
  revisePlan: (instruction: string) => Promise<void>;
  approvePlan: (input: ApprovalDecisionInput | boolean, reason?: string) => Promise<void>;
  setApprovalOverlayOpen: (value: boolean) => void;
  setConnectionState: (value: ConnectionState) => void;
  applyEvent: (event: WSEvent) => void;
  startStreamingDiagnosis: (
    alert: Alert,
    extraAlertFingerprints?: string[],
    onSessionReady?: (sessionId: string) => void,
  ) => void;
  cancelStreamingDiagnosis: () => void;
};

const DEFAULT_REVISE_INSTRUCTION = "请优化当前修复方案，补充更稳妥步骤与验证";
const DEFAULT_APPROVER = "alice";
const LOCAL_AUDIT_STORAGE_KEY = "sre_diagnosis_local_audit_v1";
const SESSION_BACKFILL_THROTTLE_MS = 1200;
const sessionBackfillLastRunAt = new Map<string, number>();
const sessionBackfillInFlight = new Set<string>();

type EventLike = Pick<WSEvent, "type" | "session_id" | "timestamp" | "data">;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeNonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const text = value.trim();
  return text.length > 0 ? text : null;
}

function buildThoughtKey(runId: string | null, node: string | null): string | null {
  if (!runId || !node) {
    return null;
  }
  return `${runId}:${node}`;
}

function getThoughtKeyFromData(data: Record<string, unknown>): string | null {
  const direct = normalizeNonEmptyString(data.thought_key);
  if (direct) {
    return direct;
  }
  return buildThoughtKey(normalizeNonEmptyString(data.run_id), normalizeNonEmptyString(data.node));
}

function toStreamingFields(
  liveThinking: LiveThinkingBlock | null,
  activeStreamingTools: StreamingToolCall[],
): Pick<DiagnosisState, "liveThinking" | "streamingText" | "streamingNode" | "activeStreamingTools"> {
  return {
    liveThinking,
    streamingText: liveThinking?.content ?? "",
    streamingNode: liveThinking?.node ?? null,
    activeStreamingTools,
  };
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
      actionType === "tool_call" || actionType === "remediate" || actionType === "conclude"
        ? actionType
        : "conclude",
    thought_key: normalizeNonEmptyString(data.thought_key),
    tool_name: typeof data.tool_name === "string" ? data.tool_name : null,
    tool_params: isRecord(data.tool_params) ? data.tool_params : null,
    confidence: typeof data.confidence === "number" ? data.confidence : null,
    next_action: typeof data.next_action === "string" ? data.next_action : null,
    thought_duration_sec: typeof data.thought_duration_sec === "number" ? data.thought_duration_sec : null,
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
  if (!session || entries.length === 0) {
    return session;
  }

  return {
    ...session,
    trace: {
      steps: [...(session.trace?.steps ?? []), ...entries],
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

function toTraceEntryFromNodeSnapshot(
  item: Record<string, unknown>,
  fallbackStep: number,
): ThinkingStep | Observation | null {
  const eventType = String(item.event_type ?? "").trim().toLowerCase();
  if (eventType === "tool_result") {
    return {
      tool: typeof item.tool === "string" ? item.tool : "tool_result",
      params: isRecord(item.params) ? item.params : {},
      result: isRecord(item.result) ? item.result : {},
      timestamp: typeof item.timestamp === "string" ? item.timestamp : new Date().toISOString(),
    };
  }

  if (eventType === "tool_call" || eventType === "thinking_step") {
    const actionType = item.action_type;
    const entry: ThinkingStep = {
      step: typeof item.step === "number" ? item.step : fallbackStep,
      timestamp: typeof item.timestamp === "string" ? item.timestamp : new Date().toISOString(),
      thought:
        typeof item.thought === "string" && item.thought.trim()
          ? item.thought
          : "诊断引擎正在扩展当前推理上下文。",
      action_type:
        actionType === "tool_call" || actionType === "remediate" || actionType === "conclude"
          ? actionType
          : "conclude",
      thought_key: normalizeNonEmptyString(item.thought_key),
      tool_name: typeof item.tool_name === "string" ? item.tool_name : null,
      tool_params: isRecord(item.tool_params) ? item.tool_params : null,
      confidence: typeof item.confidence === "number" ? item.confidence : null,
      thought_duration_sec: typeof item.thought_duration_sec === "number" ? item.thought_duration_sec : null,
    };
    if (typeof item.next_action === "string" && item.next_action.trim().length > 0) {
      entry.next_action = item.next_action;
    }
    return entry;
  }

  return null;
}

function sortByCandidateRank(left: { rank?: number }, right: { rank?: number }) {
  const lhs = Number(left.rank ?? Number.POSITIVE_INFINITY);
  const rhs = Number(right.rank ?? Number.POSITIVE_INFINITY);
  return lhs - rhs;
}

function extractRecommendedPlan(session: DiagnosisSession | undefined): RemediationPlan | null {
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
    const baselineAlert = isRecord(data.baseline_alert) ? data.baseline_alert : {};
    const postAlert = isRecord(data.post_alert) ? data.post_alert : {};
    const beforeStatus =
      typeof baselineAlert.status === "string" && baselineAlert.status.trim() ? baselineAlert.status.trim() : "unknown";
    const afterStatus =
      typeof postAlert.status === "string" && postAlert.status.trim() ? postAlert.status.trim() : "unknown";
    return `观察结果：alert_cleared=${String(data.alert_cleared ?? false)}，metrics_improved=${String(data.metrics_improved ?? false)}，告警状态 ${beforeStatus} -> ${afterStatus}`;
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

function readLocalAuditStore(): Record<string, DiagnosisLocalAuditRecord[]> {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const raw = window.localStorage.getItem(LOCAL_AUDIT_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed)) {
      return {};
    }

    return Object.fromEntries(
      Object.entries(parsed).map(([sessionId, records]) => {
        const safeRecords = Array.isArray(records)
          ? records.filter((record): record is DiagnosisLocalAuditRecord => {
              return (
                isRecord(record) &&
                typeof record.id === "string" &&
                typeof record.sessionId === "string" &&
                typeof record.eventKind === "string" &&
                typeof record.source === "string" &&
                typeof record.dedupeKey === "string" &&
                typeof record.timestamp === "string" &&
                typeof record.summary === "string" &&
                Array.isArray(record.details) &&
                typeof record.statusTone === "string"
              );
            })
          : [];
        return [sessionId, safeRecords];
      }),
    );
  } catch {
    return {};
  }
}

function writeLocalAuditStore(store: Record<string, DiagnosisLocalAuditRecord[]>) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(LOCAL_AUDIT_STORAGE_KEY, JSON.stringify(store));
}

function loadLocalAuditRecords(sessionId?: string): DiagnosisLocalAuditRecord[] {
  if (!sessionId) {
    return [];
  }
  return readLocalAuditStore()[sessionId] ?? [];
}

function persistLocalAuditRecords(sessionId: string, records: DiagnosisLocalAuditRecord[]) {
  const store = readLocalAuditStore();
  if (records.length === 0) {
    delete store[sessionId];
  } else {
    store[sessionId] = records;
  }
  writeLocalAuditStore(store);
}

function upsertLocalAuditRecord(
  records: DiagnosisLocalAuditRecord[],
  nextRecord: DiagnosisLocalAuditRecord,
): DiagnosisLocalAuditRecord[] {
  const next = [...records];
  const matchedIndex = next.findIndex((record) => record.dedupeKey === nextRecord.dedupeKey);
  if (matchedIndex >= 0) {
    next[matchedIndex] = nextRecord;
  } else {
    next.push(nextRecord);
  }

  return next.sort((left, right) => {
    return new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
  });
}

function resolveDisplayUser(user?: string) {
  const candidate = user?.trim();
  return candidate || DEFAULT_APPROVER;
}

function buildPlanDetailLines(
  session: DiagnosisSession | undefined,
  planVersion: number | null,
  timestamp: string,
) {
  const plan = extractRecommendedPlan(session);
  const lines = [
    `审批时间：${formatDateTime(timestamp)}`,
    `方案版本：${planVersion ? `v${planVersion}` : "--"}`,
    `方案 ID：${plan?.plan_id ?? "--"}`,
    `方案标题：${plan?.root_cause ?? "--"}`,
    `方案说明：${plan?.description ?? "--"}`,
  ];

  const steps = plan?.steps ?? [];
  if (steps.length === 0) {
    lines.push("执行步骤：--");
  } else {
    steps.forEach((step, index) => {
      lines.push(`步骤 ${index + 1}：${step.description}`);
    });
  }

  return lines;
}

function buildApprovalAuditRecord(
  sessionId: string,
  session: DiagnosisSession | undefined,
  input: ApprovalDecisionInput,
  planVersion: number | null,
): DiagnosisLocalAuditRecord {
  const approver = resolveDisplayUser(input.user);
  const approved = input.approved;
  const reason = input.reason?.trim();
  const timestamp = new Date().toISOString();
  const versionLabel = planVersion ? `v${planVersion}` : "v?";
  const details = buildPlanDetailLines(session, planVersion, timestamp);

  details.splice(3, 0, `审批人：${approver}`);
  details.splice(4, 0, `审批动作：${approved ? "同意，通过执行" : "拒绝执行"}`);
  if (!approved && reason) {
    details.splice(5, 0, `拒绝原因：${reason}`);
  }

  return {
    id: `local-audit-${approved ? "approved" : "rejected"}-${Date.now()}`,
    sessionId,
    eventKind: "approval_result",
    source: approved ? "optimistic" : "local_audit",
    dedupeKey: `approval-result-${approved ? "approved" : "rejected"}-${versionLabel}`,
    timestamp,
    summary: approved
      ? `[系统] 已审批，通过执行（${versionLabel}，审批人 ${approver}）`
      : `[系统] 已审批，拒绝执行（原因：${reason || "--"}）`,
    details,
    statusTone: approved ? "success" : "danger",
  };
}

function shouldOpenApprovalOverlay(
  session: DiagnosisSession | undefined,
  approvalState: Pick<DiagnosisState, "hasPlan">,
) {
  return session?.status === "approval_required" && approvalState.hasPlan;
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
    const newEvents = Array.isArray(incomingEvents) ? incomingEvents : [];
    const mergedEvents = mergeSessionEvents(snapshot.events, newEvents);
    const nextSession = session ?? snapshot.session;
    const approvalState = deriveApprovalState(nextSession ?? undefined, mergedEvents);
    const traceStatus: TraceStatus = nextSession?.trace?.steps?.length ? "ready" : "empty";
    useDiagnosisStore.setState((state) => ({
      session: nextSession ?? undefined,
      events: mergedEvents,
      traceStatus,
      messages: mergeEventMessages(state.messages, newEvents, sessionId),
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
  let latestPlanMissingReason: string | undefined;

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
    if (stage === "plan_unavailable") {
      const message = normalizeNonEmptyString(data.message);
      if (message) {
        latestPlanMissingReason = message;
      }
      return;
    }
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
  const planMissingReason = hasPlan
    ? undefined
    : latestPlanMissingReason ?? "当前会话尚未产出修复计划，请先完成诊断或切换会话。";

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

export const useDiagnosisStore = create<DiagnosisState>((set, get) => ({
  session: undefined,
  activeSessionId: undefined,
  messages: [],
  events: [],
  localAuditRecords: [],
  chatContextApplied: false,
  chatContextMeta: undefined,
  isLoadingSession: false,
  bootstrapStatus: "idle",
  traceStatus: "unknown",
  isSendingMessage: false,
  isRevisingPlan: false,
  isApprovingPlan: false,
  approvalOverlayOpen: false,
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
  liveThinking: null,
  liveFinalAnswer: null,
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
      approvalOverlayOpen: false,
      messages: [],
      events: [],
      localAuditRecords: [],
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
      liveThinking: null,
      liveFinalAnswer: null,
      streamingText: "",
      streamingNode: null,
      isStreamingDiagnosis: false,
      activeStreamingTools: [],
      streamingAbortController: null,
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
          localAuditRecords: [],
          currentPlanVersion: null,
          latestPlanVersion: null,
          approvedPlanVersion: null,
          canApprove: false,
          approvalBlockReason: undefined,
          hasPlan: false,
          planMissingReason: undefined,
          effectiveReviseInstruction: undefined,
          isLoadingSession: false,
          bootstrapStatus: "empty",
          traceStatus: "unknown",
          isSendingMessage: false,
          isRevisingPlan: false,
          isApprovingPlan: false,
          approvalOverlayOpen: false,
          chatContextApplied: false,
          chatContextMeta: undefined,
          alertSnapshot: null,
          topologyContext: null,
          liveThinking: null,
          liveFinalAnswer: null,
          streamingText: "",
          streamingNode: null,
          isStreamingDiagnosis: false,
          activeStreamingTools: [],
          streamingAbortController: null,
          error: undefined,
        });
        return;
      }

      const messages = await apiClient.getChatHistory(resolvedSessionId);
      const events = await apiClient.getSessionEvents(resolvedSessionId).catch(() => []);
      const traceStatus: TraceStatus = (session.trace?.steps ?? []).length > 0 ? "ready" : "empty";
      const approvalState = deriveApprovalState(session, events);
      const localAuditRecords = loadLocalAuditRecords(resolvedSessionId);
      const startedEvent = events.find((event) => event.type === "diagnosis_started");
      const startedData = startedEvent && isRecord(startedEvent.data) ? startedEvent.data : {};
      const historicalAlert = (startedData.alert as DiagnosisStartedData["alert"]) ?? null;
      const historicalTopology = (startedData.topology as DiagnosisStartedData["topology"]) ?? null;

      set({
        session,
        activeSessionId: resolvedSessionId,
        messages,
        events,
        localAuditRecords,
        isLoadingSession: false,
        bootstrapStatus: "ready",
        traceStatus,
        isSendingMessage: false,
        isRevisingPlan: false,
        isApprovingPlan: false,
        approvalOverlayOpen: shouldOpenApprovalOverlay(session, approvalState),
        ...approvalState,
        effectiveReviseInstruction: undefined,
        chatContextApplied: false,
        chatContextMeta: undefined,
        alertSnapshot: historicalAlert,
        topologyContext: historicalTopology,
        liveThinking: null,
        liveFinalAnswer: null,
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
        localAuditRecords: [],
        currentPlanVersion: null,
        latestPlanVersion: null,
        approvedPlanVersion: null,
        canApprove: false,
        approvalBlockReason: undefined,
        hasPlan: false,
        planMissingReason: undefined,
        effectiveReviseInstruction: undefined,
        approvalOverlayOpen: false,
        chatContextApplied: false,
        chatContextMeta: undefined,
        alertSnapshot: null,
        topologyContext: null,
        liveThinking: null,
        liveFinalAnswer: null,
        streamingText: "",
        streamingNode: null,
        isStreamingDiagnosis: false,
        activeStreamingTools: [],
        streamingAbortController: null,
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
    const text = instruction.trim();
    const effectiveInstruction = text || DEFAULT_REVISE_INSTRUCTION;
    if (!sessionId) {
      return;
    }

    set({
      isRevisingPlan: true,
      error: undefined,
      effectiveReviseInstruction: effectiveInstruction,
    });

    try {
      const payload = await apiClient.reviseRemediationPlan(sessionId, effectiveInstruction, basePlanVersion);
      const events = await apiClient.getSessionEvents(sessionId).catch(() => []);
      const approvalState = deriveApprovalState(payload.session, events);
      set((state) => ({
        session: payload.session,
        events,
        localAuditRecords: state.localAuditRecords,
        ...approvalState,
        approvalOverlayOpen: shouldOpenApprovalOverlay(payload.session, approvalState),
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
  approvePlan: async (inputOrApproved: ApprovalDecisionInput | boolean, fallbackReason?: string) => {
    const input: ApprovalDecisionInput =
      typeof inputOrApproved === "boolean"
        ? { approved: inputOrApproved, reason: fallbackReason }
        : inputOrApproved;
    const sessionId = get().activeSessionId;
    const planVersion = get().latestPlanVersion ?? undefined;
    const reason = input.reason?.trim() || (!input.approved ? "需要人工复核" : undefined);
    const user = resolveDisplayUser(input.user);

    if (!sessionId) {
      return;
    }

    if (!input.approved && !reason) {
      set({ error: "拒绝执行时请填写原因。" });
      return;
    }

    set({
      isApprovingPlan: true,
      error: undefined,
      approvalOverlayOpen: false,
    });

    try {
      await apiClient.approveRemediation(sessionId, input.approved, user, planVersion);
    } catch (error) {
      set({
        isApprovingPlan: false,
        error: error instanceof Error ? error.message : "审批操作失败",
        approvalOverlayOpen: true,
      });
      throw error;
    }

    const approvalRecord = buildApprovalAuditRecord(
      sessionId,
      get().session,
      { approved: input.approved, reason, user },
      planVersion ?? null,
    );
    const nextLocalAuditRecords = upsertLocalAuditRecord(get().localAuditRecords, approvalRecord);
    persistLocalAuditRecords(sessionId, nextLocalAuditRecords);

    const session = await apiClient.getDiagnosisSession(sessionId).catch(() => get().session ?? null);
    const events = await apiClient.getSessionEvents(sessionId).catch(() => []);
    const approvalState = deriveApprovalState(session ?? undefined, events);

    set({
      session: session ?? undefined,
      events,
      localAuditRecords: nextLocalAuditRecords,
      ...approvalState,
      isApprovingPlan: false,
      approvalOverlayOpen: false,
    });
  },
  setApprovalOverlayOpen: (approvalOverlayOpen) => set({ approvalOverlayOpen }),
  setConnectionState: (connectionState) => set({ connectionState }),
  applyEvent: (event) => {
    const targetSessionId = event.session_id || get().activeSessionId;
    set((state) => {
      const activeSessionId = state.activeSessionId ?? state.session?.session_id;
      if (activeSessionId && targetSessionId && targetSessionId !== activeSessionId) {
        return state;
      }

      const currentTrace = state.session?.trace?.steps ?? [];
      const nextEntries = [toThinkingStep(event, currentTrace.length + 1), toObservation(event)].filter(
        (entry): entry is ThinkingStep | Observation => Boolean(entry),
      );
      let nextSession = appendTraceEntries(state.session, nextEntries);
      let nextAlertSnapshot = state.alertSnapshot;
      let nextTopologyContext = state.topologyContext;
      let nextLiveThinking = state.liveThinking;
      let nextLiveFinalAnswer = state.liveFinalAnswer;
      let nextActiveStreamingTools = state.activeStreamingTools;
      const data = isRecord(event.data) ? event.data : {};
      const eventSource = normalizeNonEmptyString(data._stream_source);
      const thoughtKey = getThoughtKeyFromData(data);
      const runId = normalizeNonEmptyString(data.run_id);
      const node = normalizeNonEmptyString(data.node);

      if (event.type === "diagnosis_started") {
        nextAlertSnapshot = (data.alert as DiagnosisStartedData["alert"]) ?? null;
        nextTopologyContext = (data.topology as DiagnosisStartedData["topology"]) ?? null;
      }
      if (event.type === "token_delta") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        const content = String(data.content ?? "");
        const phase = normalizeNonEmptyString(data.phase);
        const streamChannel = normalizeNonEmptyString(data.stream_channel);
        if (!content) {
          return {
            ...state,
            isStreamingDiagnosis: true,
          };
        }

        if (phase === "final" && streamChannel === "content") {
          const currentFinal = nextLiveFinalAnswer;
          nextLiveFinalAnswer = {
            id: currentFinal?.id ?? `live-final-${event.session_id || targetSessionId || event.timestamp}`,
            timestamp: currentFinal?.timestamp ?? event.timestamp,
            content: `${currentFinal?.content ?? ""}${content}`,
            status: "streaming",
          };
          return {
            ...state,
            liveFinalAnswer: nextLiveFinalAnswer,
            isStreamingDiagnosis: true,
          };
        }

        const resolvedThoughtKey = thoughtKey ?? nextLiveThinking?.thought_key ?? buildThoughtKey(runId, node);
        if (!resolvedThoughtKey) {
          return {
            ...state,
            isStreamingDiagnosis: true,
          };
        }

        const isSameThought = nextLiveThinking?.thought_key === resolvedThoughtKey;
        const previousContent = isSameThought ? nextLiveThinking?.content ?? "" : "";
        const shouldReplacePlaceholder = previousContent.includes("诊断引擎正在分析当前证据");
        nextLiveThinking = {
          thought_key: resolvedThoughtKey,
          run_id: runId ?? nextLiveThinking?.run_id ?? null,
          node: node ?? nextLiveThinking?.node ?? null,
          timestamp: nextLiveThinking?.timestamp ?? event.timestamp,
          content: `${isSameThought && !shouldReplacePlaceholder ? previousContent : ""}${content}`,
          status: "thinking",
          thought_duration_sec: null,
          next_action: isSameThought ? nextLiveThinking?.next_action ?? null : null,
          tool_name: isSameThought ? nextLiveThinking?.tool_name ?? null : null,
          active_tools: nextActiveStreamingTools,
        };
        return {
          ...state,
          isStreamingDiagnosis: true,
          ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
        };
      }
      if (event.type === "node_started") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        const resolvedThoughtKey = thoughtKey ?? buildThoughtKey(runId, node);
        const thoughtPlaceholder =
          normalizeNonEmptyString(data.thought_placeholder) ??
          normalizeNonEmptyString(data.message) ??
          "诊断引擎正在分析当前证据并规划下一步行动。";
        nextActiveStreamingTools =
          nextLiveThinking?.thought_key === resolvedThoughtKey ? nextLiveThinking.active_tools : [];
        nextLiveThinking = resolvedThoughtKey
          ? {
              thought_key: resolvedThoughtKey,
              run_id: runId,
              node,
              timestamp: normalizeNonEmptyString(data.started_at) ?? event.timestamp,
              content: thoughtPlaceholder,
              status: "thinking",
              thought_duration_sec: null,
              next_action: null,
              tool_name: null,
              active_tools: nextActiveStreamingTools,
            }
          : null;
        return {
          ...state,
          isStreamingDiagnosis: true,
          ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
        };
      }
      if (event.type === "node_completed") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        const snapshotItems = Array.isArray(data.new_trace_items)
          ? data.new_trace_items.filter(isRecord)
          : [];
        const existingThoughtKeys = new Set(
          currentTrace.flatMap((entry) =>
            "thought" in entry && typeof entry.thought_key === "string" ? [entry.thought_key] : [],
          ),
        );
        const snapshotEntries = snapshotItems
          .map((item, index) => toTraceEntryFromNodeSnapshot(item, currentTrace.length + index + 1))
          .filter((entry): entry is ThinkingStep | Observation => Boolean(entry))
          .filter((entry) => {
            if (!("thought" in entry) || typeof entry.thought_key !== "string") {
              return true;
            }
            if (existingThoughtKeys.has(entry.thought_key)) {
              return false;
            }
            existingThoughtKeys.add(entry.thought_key);
            return true;
          });
        const mergedEntries = [...nextEntries, ...snapshotEntries];
        nextSession = appendTraceEntries(nextSession, snapshotEntries);
        const nextEvents = mergeSessionEvents(state.events, [event as SessionEvent]);
        const approvalState = deriveApprovalState(nextSession, nextEvents);
        const completionError = getEventError(event) ?? state.error;
        const completedThoughtKey = thoughtKey ?? nextLiveThinking?.thought_key;
        const snapshotHasCompletedThought = snapshotEntries.some(
          (entry) =>
            "thought" in entry &&
            typeof entry.thought_key === "string" &&
            entry.thought_key === completedThoughtKey,
        );
        if (!snapshotHasCompletedThought && nextLiveThinking && nextLiveThinking.thought_key === completedThoughtKey) {
          nextLiveThinking = {
            ...nextLiveThinking,
            status: "completed",
            thought_duration_sec:
              typeof data.thought_duration_sec === "number" ? data.thought_duration_sec : nextLiveThinking.thought_duration_sec,
            active_tools: [],
          };
        } else {
          nextLiveThinking = null;
        }

        return {
          session: nextSession,
          events: nextEvents,
          localAuditRecords: state.localAuditRecords,
          ...approvalState,
          approvalOverlayOpen:
            nextSession?.status === "approval_required" ? state.approvalOverlayOpen : false,
          effectiveReviseInstruction: state.effectiveReviseInstruction,
          messages: state.messages,
          alertSnapshot: nextAlertSnapshot,
          topologyContext: nextTopologyContext,
          liveFinalAnswer: nextLiveFinalAnswer,
          error: completionError,
          traceStatus:
            mergedEntries.length > 0 ? "ready" : state.traceStatus,
          isStreamingDiagnosis: true,
          ...toStreamingFields(nextLiveThinking, []),
          streamingAbortController: state.streamingAbortController,
        };
      }
      if (event.type === "tool_started") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        const tool = String(data.tool ?? "");
        if (!tool) {
          return state;
        }
        const resolvedThoughtKey = thoughtKey ?? nextLiveThinking?.thought_key ?? buildThoughtKey(runId, node);
        const nextTool: StreamingToolCall = {
          tool,
          params: isRecord(data.params) ? data.params : {},
          thought_key: resolvedThoughtKey,
          run_id: runId,
          node,
        };
        const toolExists = nextActiveStreamingTools.some(
          (item) => item.tool === nextTool.tool && item.thought_key === nextTool.thought_key,
        );
        nextActiveStreamingTools = toolExists ? nextActiveStreamingTools : [...nextActiveStreamingTools, nextTool];
        if (nextLiveThinking && (!resolvedThoughtKey || nextLiveThinking.thought_key === resolvedThoughtKey)) {
          nextLiveThinking = {
            ...nextLiveThinking,
            tool_name: nextLiveThinking.tool_name ?? tool,
            active_tools: nextActiveStreamingTools,
          };
        }
        return {
          ...state,
          isStreamingDiagnosis: true,
          ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
        };
      }
      if (event.type === "tool_completed") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        const tool = String(data.tool ?? "");
        const resolvedThoughtKey = thoughtKey ?? nextLiveThinking?.thought_key ?? buildThoughtKey(runId, node);
        nextActiveStreamingTools = nextActiveStreamingTools.filter((item) => {
          if (item.tool !== tool) {
            return true;
          }
          if (resolvedThoughtKey && item.thought_key && item.thought_key !== resolvedThoughtKey) {
            return true;
          }
          return false;
        });
        if (nextLiveThinking && (!resolvedThoughtKey || nextLiveThinking.thought_key === resolvedThoughtKey)) {
          nextLiveThinking = {
            ...nextLiveThinking,
            active_tools: nextActiveStreamingTools,
          };
        }
        return {
          ...state,
          ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
        };
      }
      if (event.type === "done") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        return {
          ...state,
          isStreamingDiagnosis: false,
          liveFinalAnswer: state.liveFinalAnswer
            ? { ...state.liveFinalAnswer, status: "completed" }
            : state.liveFinalAnswer,
          ...toStreamingFields(null, []),
          streamingAbortController: null,
        };
      }
      if (event.type === "error" && !nextSession) {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        return {
          ...state,
          isStreamingDiagnosis: false,
          ...toStreamingFields(null, []),
          streamingAbortController: null,
          liveFinalAnswer: null,
          error: getEventError(event) ?? state.error,
        };
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
      const approvalOverlayOpen =
        event.type === "approval_required" || event.type === "plan_revised"
          ? shouldOpenApprovalOverlay(nextSession, approvalState)
          : nextSession?.status === "approval_required"
            ? state.approvalOverlayOpen
            : false;

      return {
        session: nextSession,
        events: nextEvents,
        localAuditRecords: state.localAuditRecords,
        ...approvalState,
        approvalOverlayOpen,
        effectiveReviseInstruction: state.effectiveReviseInstruction,
        messages: mergeEventMessages(state.messages, [event], targetSessionId),
        alertSnapshot: nextAlertSnapshot,
        topologyContext: nextTopologyContext,
        liveFinalAnswer: nextLiveFinalAnswer,
        ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
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
  startStreamingDiagnosis: (alert, extraAlertFingerprints = [], onSessionReady) => {
    const controller = new AbortController();
    set({
      liveThinking: null,
      liveFinalAnswer: null,
      streamingText: "",
      streamingNode: null,
      isStreamingDiagnosis: true,
      activeStreamingTools: [],
      streamingAbortController: controller,
      error: undefined,
    });

    const run = async () => {
      let sessionFired = false;
      try {
        await streamDiagnosis(
          alert,
          extraAlertFingerprints,
          (event) => {
            const data = isRecord(event.data) ? event.data : {};
            const timestamp = new Date().toISOString();
            if (!sessionFired && event.session_id) {
              sessionFired = true;
              set({ activeSessionId: event.session_id });
              onSessionReady?.(event.session_id);
            }

            if (
              event.session_id &&
              [
                "thinking_step",
                "tool_call",
                "tool_result",
                "diagnosis_started",
                "token_delta",
                "node_started",
                "node_completed",
                "tool_started",
                "tool_completed",
                "diagnosis_result",
                "approval_required",
                "plan_revised",
                "remediation_progress",
                "escalation_required",
                "observation_result",
                "error",
                "execution_mocked",
                "done",
              ].includes(event.type)
            ) {
              get().applyEvent({
                schema_version: "1",
                type: event.type as WSEvent["type"],
                session_id: event.session_id,
                timestamp,
                data: {
                  ...data,
                  _stream_source: "sse",
                },
              });
            }
          },
          controller.signal,
        );
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          set({
            isStreamingDiagnosis: false,
            liveThinking: null,
            liveFinalAnswer: null,
            streamingAbortController: null,
            error: error instanceof Error ? error.message : "streaming diagnosis failed",
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
      liveThinking: null,
      liveFinalAnswer: null,
      streamingText: "",
      streamingNode: null,
      activeStreamingTools: [],
      streamingAbortController: null,
    });
  },
}));
