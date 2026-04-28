import { create } from "zustand";

import {
  apiClient,
  streamDiagnosis,
  type SessionResolveSource,
  type SessionResolveState,
} from "../api/client";
import type {
  Alert,
  ChatMessage,
  DiagnosisResult,
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
type StreamingPhase =
  | "idle"
  | "bootstrapping"
  | "waiting_first_content"
  | "streaming_thought"
  | "streaming_final"
  | "completed"
  | "error";

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
  sessionResolveState: SessionResolveState;
  sessionResolveSource: SessionResolveSource;
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
  completedThinkingRounds: LiveThinkingBlock[];
  liveThinking: LiveThinkingBlock | null;
  liveFinalAnswer: LiveFinalAnswerBlock | null;
  streamingText: string;
  streamingNode: string | null;
  isStreamingDiagnosis: boolean;
  streamingPhase: StreamingPhase;
  streamSequenceCounter: number;
  roundSequenceCounter: number;
  activeStreamingTools: StreamingToolCall[];
  streamingAbortController: AbortController | null;
  bootstrapSession: (sessionId?: string) => Promise<void>;
  reconcileSession: (sessionId?: string) => Promise<void>;
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
  ) => string;
  cancelStreamingDiagnosis: () => void;
};

const DEFAULT_REVISE_INSTRUCTION = "请优化当前修复方案，补充更稳妥步骤与验证";
const DEFAULT_APPROVER = "alice";
const LOCAL_AUDIT_STORAGE_KEY = "sre_diagnosis_local_audit_v1";
const SESSION_BACKFILL_THROTTLE_MS = 1200;
const PENDING_SESSION_PREFIX = "pending-";
const TERMINAL_SESSION_STATUSES = new Set([
  "resolved",
  "closed",
  "failed",
  "timeout",
  "escalated",
  "rejected",
]);
const sessionBackfillLastRunAt = new Map<string, number>();
const sessionBackfillInFlight = new Set<string>();
type EventLike = Pick<WSEvent, "type" | "session_id" | "timestamp" | "data">;
const APPROVAL_EVENT_POLL_MS = 1000;

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

function isPendingSessionId(value: string | null | undefined): value is string {
  return typeof value === "string" && value.startsWith(PENDING_SESSION_PREFIX);
}

function buildPendingSession(alert: Alert, sessionId: string): DiagnosisSession {
  return {
    session_id: sessionId,
    alert,
    status: "diagnosing",
    diagnosis_result: null,
    trace: { steps: [] },
    bootstrap: null,
    duration_seconds: 0,
    outcome: null,
    remediation_evidence: null,
  };
}

function getThoughtKeyFromData(data: Record<string, unknown>): string | null {
  const direct = normalizeNonEmptyString(data.thought_key);
  if (direct) {
    return direct;
  }
  return buildThoughtKey(normalizeNonEmptyString(data.run_id), normalizeNonEmptyString(data.node));
}

function getRoundIdFromData(data: Record<string, unknown>): string | null {
  return (
    normalizeNonEmptyString(data.round_id) ??
    normalizeNonEmptyString(data.node_invocation_id) ??
    normalizeNonEmptyString(data.invocation_id)
  );
}

function buildFallbackRoundId({
  explicitRoundId,
  thoughtKey,
  runId,
  node,
  timestamp,
}: {
  explicitRoundId?: string | null;
  thoughtKey?: string | null;
  runId?: string | null;
  node?: string | null;
  timestamp: string;
}): string | null {
  if (explicitRoundId) {
    return explicitRoundId;
  }
  const base = thoughtKey ?? buildThoughtKey(runId ?? null, node ?? null) ?? node ?? runId;
  return base ? `${base}@${timestamp}` : null;
}

type RoundIdentity = {
  roundId?: string | null;
  thoughtKey?: string | null;
  runId?: string | null;
  node?: string | null;
};

function hasRoundIdentity(identity: RoundIdentity): boolean {
  return Boolean(identity.roundId || identity.thoughtKey || identity.runId || identity.node);
}

function matchesLiveThinkingRound(
  liveThinking: LiveThinkingBlock | null,
  identity: RoundIdentity,
  allowWhenIdentityMissing = true,
): boolean {
  if (!liveThinking) {
    return false;
  }
  if (!hasRoundIdentity(identity)) {
    return allowWhenIdentityMissing;
  }
  if (identity.roundId && liveThinking.round_id && identity.roundId !== liveThinking.round_id) {
    return false;
  }
  if (identity.thoughtKey && liveThinking.thought_key !== identity.thoughtKey) {
    return false;
  }
  if (identity.runId && liveThinking.run_id && liveThinking.run_id !== identity.runId) {
    return false;
  }
  if (identity.node && liveThinking.node && liveThinking.node !== identity.node) {
    return false;
  }
  return true;
}

function buildTraceEntryDedupeKey(entry: ThinkingStep | Observation): string {
  if ("thought" in entry) {
    const thoughtKey = normalizeNonEmptyString(entry.thought_key) ?? "no-thought-key";
    const step = Number.isFinite(entry.step) ? String(entry.step) : "na";
    return `thinking:${thoughtKey}:${step}:${entry.timestamp}`;
  }
  return `observation:${entry.tool}:${entry.timestamp}`;
}

function buildThinkingRoundDedupeKey(round: LiveThinkingBlock): string {
  const roundId = normalizeNonEmptyString(round.round_id);
  if (roundId) {
    return roundId;
  }
  return `${round.thought_key}@${round.timestamp}`;
}

function appendCompletedThinkingRound(
  rounds: LiveThinkingBlock[],
  round: LiveThinkingBlock | null,
): LiveThinkingBlock[] {
  if (!round || round.status !== "completed") {
    return rounds;
  }
  if ((round.node ?? "").trim().toLowerCase() === "bootstrap") {
    return rounds;
  }
  if (round.content.trim().length === 0) {
    return rounds;
  }
  const dedupeKey = buildThinkingRoundDedupeKey(round);
  const baseRound: LiveThinkingBlock = {
    ...round,
    status: "completed",
    active_tools: [],
  };
  const next = [...rounds.filter((item) => buildThinkingRoundDedupeKey(item) !== dedupeKey), baseRound];
  return sortCompletedThinkingRounds(next);
}

function consumeCompletedRoundsBySnapshot(
  rounds: LiveThinkingBlock[],
  snapshotEntries: Array<ThinkingStep | Observation>,
): LiveThinkingBlock[] {
  if (rounds.length === 0 || snapshotEntries.length === 0) {
    return rounds;
  }
  const snapshotThinkingCounts = new Map<string, number>();
  snapshotEntries.forEach((entry) => {
    if (!("thought" in entry) || typeof entry.thought_key !== "string") {
      return;
    }
    const normalizedThoughtKey = entry.thought_key.trim();
    if (!normalizedThoughtKey) {
      return;
    }
    snapshotThinkingCounts.set(
      normalizedThoughtKey,
      (snapshotThinkingCounts.get(normalizedThoughtKey) ?? 0) + 1,
    );
  });
  if (snapshotThinkingCounts.size === 0) {
    return rounds;
  }

  const nextRounds: LiveThinkingBlock[] = [];
  rounds.forEach((round) => {
    const remaining = snapshotThinkingCounts.get(round.thought_key) ?? 0;
    if (remaining > 0) {
      snapshotThinkingCounts.set(round.thought_key, remaining - 1);
      return;
    }
    nextRounds.push(round);
  });
  return nextRounds;
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

function sortCompletedThinkingRounds(
  rounds: LiveThinkingBlock[],
): LiveThinkingBlock[] {
  const next = [...rounds];
  next.sort((left, right) => {
    const leftRoundSeq = typeof left.round_seq === "number" ? left.round_seq : null;
    const rightRoundSeq = typeof right.round_seq === "number" ? right.round_seq : null;
    if (leftRoundSeq !== null && rightRoundSeq !== null && leftRoundSeq !== rightRoundSeq) {
      return leftRoundSeq - rightRoundSeq;
    }
    return new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
  });
  return next;
}

function wait(ms: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, ms));
}

function toReadableErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    const message = error.message.trim();
    if (message) {
      return message;
    }
  }
  return fallback;
}

function formatBootstrapPartialWarning(parts: string[]): string | undefined {
  if (parts.length === 0) {
    return undefined;
  }
  return `部分补充信息加载较慢，诊断结果仍可查看：${parts.join("；")}`;
}

function formatApprovalErrorMessage(error: unknown, approved: boolean): string {
  const suffix = toReadableErrorMessage(error, approved ? "审批操作失败" : "拒绝审批失败");
  return approved ? `修复执行审批失败：${suffix}` : `审批请求失败：${suffix}`;
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

type TerminalReason = "done" | "error" | "timeout" | "diagnosis_result";

const TERMINAL_NODE_STATUSES = new Set(["failed", "timeout", "step_timeout", "diagnosed"]);

function normalizeTerminalReason(value: unknown): TerminalReason | null {
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim().toLowerCase();
  if (normalized === "done" || normalized === "error" || normalized === "timeout" || normalized === "diagnosis_result") {
    return normalized;
  }
  return null;
}

function resolveTerminalReasonFromStatus(status: string | null): TerminalReason | null {
  const normalized = status?.trim().toLowerCase() ?? "";
  if (!normalized) {
    return null;
  }
  if (normalized === "failed") {
    return "error";
  }
  if (normalized === "timeout" || normalized === "step_timeout") {
    return "timeout";
  }
  if (normalized === "diagnosed") {
    return "diagnosis_result";
  }
  return null;
}

function resolveTerminalPhase(reason: TerminalReason): Extract<StreamingPhase, "completed" | "error"> {
  return reason === "error" || reason === "timeout" ? "error" : "completed";
}

function buildTerminalThinkingSummary({
  reason,
  status,
  summary,
  error,
}: {
  reason: TerminalReason;
  status?: string | null;
  summary?: string | null;
  error?: string | null;
}): string {
  const normalizedSummary = normalizeNonEmptyString(summary);
  if (normalizedSummary) {
    return normalizedSummary;
  }
  const normalizedError = normalizeNonEmptyString(error);
  if (reason === "error" && normalizedError) {
    return `诊断失败：${normalizedError}`;
  }
  if (reason === "timeout") {
    const normalizedStatus = (status ?? "").trim().toLowerCase();
    if (normalizedStatus === "step_timeout") {
      return "推理步骤超时，已基于当前证据结束。";
    }
    return "诊断超时，已基于当前证据结束。";
  }
  if (reason === "diagnosis_result") {
    return "诊断结果已生成。";
  }
  if (normalizedError) {
    return `诊断结束：${normalizedError}`;
  }
  return "思考完成。";
}

function completeLiveThinking(
  liveThinking: LiveThinkingBlock | null,
  fallbackSummary: string,
): LiveThinkingBlock | null {
  if (!liveThinking) {
    return null;
  }
  const existingContent = liveThinking.content.trim();
  return {
    ...liveThinking,
    status: "completed",
    content: existingContent.length > 0 ? liveThinking.content : fallbackSummary,
    active_tools: [],
  };
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

function buildDiagnosisResultFromCandidatesPayload(
  payload: Record<string, unknown>,
  previous: DiagnosisSession["diagnosis_result"] | null | undefined,
): NonNullable<DiagnosisSession["diagnosis_result"]> {
  const rankedCandidates = Array.isArray(payload.ranked_candidates) ? (payload.ranked_candidates as DiagnosisResult["ranked_candidates"]) : [];
  const hypotheses = Array.isArray(payload.hypotheses) ? (payload.hypotheses as DiagnosisResult["hypotheses"]) : [];
  const confidence = typeof payload.confidence === "number" ? payload.confidence : (previous?.confidence ?? 0);
  const diagnosisCertainty =
    payload.diagnosis_certainty === "confirmed" ||
    payload.diagnosis_certainty === "probable" ||
    payload.diagnosis_certainty === "ambiguous"
      ? payload.diagnosis_certainty
      : (previous?.diagnosis_certainty ?? "ambiguous");
  const triagePriority =
    payload.triage_priority === "P0" ||
    payload.triage_priority === "P1" ||
    payload.triage_priority === "P2" ||
    payload.triage_priority === "P3"
      ? payload.triage_priority
      : (previous?.triage_priority ?? "P2");
  const affectedServices = Array.isArray(payload.affected_services)
    ? payload.affected_services.filter((item): item is string => typeof item === "string")
    : (previous?.affected_services ?? []);
  const impactSummary = typeof payload.impact_summary === "string" ? payload.impact_summary : (previous?.impact_summary ?? "");

  return {
    root_cause: previous?.root_cause ?? "",
    root_cause_layer: previous?.root_cause_layer ?? "platform",
    root_cause_entities: previous?.root_cause_entities ?? [],
    confidence,
    next_action: previous?.next_action ?? null,
    hypotheses,
    propagation_chain: previous?.propagation_chain ?? [],
    impact_summary: impactSummary,
    affected_services: affectedServices,
    triage_priority: triagePriority,
    ranked_candidates: rankedCandidates,
    diagnosis_certainty: diagnosisCertainty,
    recommended_fix: previous?.recommended_fix ?? null,
  };
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
  const data = isRecord(event.data) ? event.data : {};
  if (event.type === "remediation_progress") {
    const stage = String(data.stage ?? "").trim();
    const seconds = new Date(event.timestamp).getTime() / 1000;
    const rounded = Math.round(seconds / 2) * 2;
    return `remediation:${stage}:${rounded}`;
  }
  const { _stream_source: _, _stream_seq: __, ...cleanData } = data;
  return `${event.type}:${event.timestamp}:${JSON.stringify(cleanData)}`;
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
  if (
    event.type === "diagnosis_candidates_ready" ||
    event.type === "diagnosis_result" ||
    event.type === "approval_required" ||
    event.type === "plan_revised"
  ) {
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
    "审批反馈：已经完成执行确认",
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
      ? `[系统] 已经完成执行确认（${versionLabel}，审批人 ${approver}）`
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
  sessionResolveState: "resolved",
  sessionResolveSource: "none",
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
  completedThinkingRounds: [],
  liveThinking: null,
  liveFinalAnswer: null,
  streamingText: "",
  streamingNode: null,
  isStreamingDiagnosis: false,
  streamingPhase: "idle",
  streamSequenceCounter: 0,
  roundSequenceCounter: 0,
  activeStreamingTools: [],
  streamingAbortController: null,
  connectionState: "closed",
  error: undefined,
  bootstrapSession: async (sessionId) => {
    const explicitSessionId = sessionId?.trim();
    const snapshot = get();
    const currentActiveSessionId = snapshot.activeSessionId ?? snapshot.session?.session_id;
    const hasMatchingLiveSession =
      Boolean(explicitSessionId) &&
      Boolean(currentActiveSessionId) &&
      (explicitSessionId === currentActiveSessionId ||
        (isPendingSessionId(explicitSessionId) && isPendingSessionId(currentActiveSessionId)));

    if (snapshot.isStreamingDiagnosis && (!explicitSessionId || hasMatchingLiveSession)) {
      return;
    }

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
      completedThinkingRounds: [],
      liveThinking: null,
      liveFinalAnswer: null,
      streamingText: "",
      streamingNode: null,
      isStreamingDiagnosis: false,
      streamingPhase: "idle",
      streamSequenceCounter: 0,
      roundSequenceCounter: 0,
      activeStreamingTools: [],
      streamingAbortController: null,
      sessionResolveState: "resolved",
      sessionResolveSource: explicitSessionId ? "url" : "none",
    });

    try {
      const resolution = await apiClient.resolveActiveSession(explicitSessionId);
      const session = resolution.session;
      const resolvedSessionId = session?.session_id;

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
          completedThinkingRounds: [],
          liveThinking: null,
          liveFinalAnswer: null,
          streamingText: "",
          streamingNode: null,
          isStreamingDiagnosis: false,
          streamingPhase: "idle",
          streamSequenceCounter: 0,
          roundSequenceCounter: 0,
          activeStreamingTools: [],
          streamingAbortController: null,
          sessionResolveState: resolution.state,
          sessionResolveSource: resolution.source,
          error: undefined,
        });
        return;
      }

      const warningParts: string[] = [];
      if (resolution.state === "stale_redirected") {
        warningParts.push("会话已失效，已自动切换到最新可用会话。");
      }
      const [messagesResult, eventsResult] = await Promise.allSettled([
        apiClient.getChatHistory(resolvedSessionId),
        apiClient.getSessionEvents(resolvedSessionId),
      ]);
      const messages =
        messagesResult.status === "fulfilled"
          ? messagesResult.value
          : [];
      if (messagesResult.status === "rejected") {
        warningParts.push(
          `对话历史未完全加载（${toReadableErrorMessage(messagesResult.reason, "history unavailable")})`,
        );
      }
      const events =
        eventsResult.status === "fulfilled"
          ? eventsResult.value
          : [];
      if (eventsResult.status === "rejected") {
        warningParts.push(
          `会话事件未完全加载（${toReadableErrorMessage(eventsResult.reason, "events unavailable")})`,
        );
      }
      const traceStatus: TraceStatus = (session.trace?.steps ?? []).length > 0 ? "ready" : "empty";
      const approvalState = deriveApprovalState(session, events);
      const localAuditRecords = loadLocalAuditRecords(resolvedSessionId);
      const startedEvent = [...events].reverse().find((event) => event.type === "diagnosis_started");
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
        completedThinkingRounds: [],
        liveThinking: null,
        liveFinalAnswer: null,
        streamingPhase: "idle",
        streamSequenceCounter: 0,
        roundSequenceCounter: 0,
        sessionResolveState: resolution.state,
        sessionResolveSource: resolution.source,
        error: formatBootstrapPartialWarning(warningParts),
      });
    } catch (error) {
      set({
        error: toReadableErrorMessage(error, "加载会话失败"),
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
        completedThinkingRounds: [],
        liveThinking: null,
        liveFinalAnswer: null,
        streamingText: "",
        streamingNode: null,
        isStreamingDiagnosis: false,
        streamingPhase: "error",
        streamSequenceCounter: 0,
        roundSequenceCounter: 0,
        activeStreamingTools: [],
        streamingAbortController: null,
        sessionResolveState: "resolved",
        sessionResolveSource: explicitSessionId ? "url" : "none",
      });
    }
  },
  reconcileSession: async (sessionId) => {
    const resolvedSessionId = sessionId?.trim() || get().activeSessionId || get().session?.session_id;
    if (!resolvedSessionId) {
      return;
    }
    await runSessionBackfill(resolvedSessionId);
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

    const approvalRecord = buildApprovalAuditRecord(
      sessionId,
      get().session,
      { approved: input.approved, reason, user },
      planVersion ?? null,
    );
    const nextLocalAuditRecords = upsertLocalAuditRecord(get().localAuditRecords, approvalRecord);
    persistLocalAuditRecords(sessionId, nextLocalAuditRecords);

    set((state) => ({
      localAuditRecords: nextLocalAuditRecords,
      session: input.approved && state.session
        ? { ...state.session, status: "remediating" }
        : state.session,
      approvalOverlayOpen: false,
    }));

    let keepPollingApprovalEvents = input.approved;
    const pollApprovalEvents = async () => {
      while (keepPollingApprovalEvents) {
        await wait(APPROVAL_EVENT_POLL_MS);
        if (!keepPollingApprovalEvents) {
          return;
        }
        try {
          const [polledSession, polledEvents] = await Promise.all([
            apiClient.getDiagnosisSession(sessionId).catch(() => get().session ?? null),
            apiClient.getSessionEvents(sessionId).catch(() => get().events),
          ]);
          const approvalState = deriveApprovalState(polledSession ?? undefined, polledEvents);
          set({
            session: polledSession ?? undefined,
            events: polledEvents,
            localAuditRecords: nextLocalAuditRecords,
            ...approvalState,
            isApprovingPlan: true,
            approvalOverlayOpen: false,
          });
        } catch {
          // Keep the approval request path authoritative; polling is only a live-progress bridge.
        }
      }
    };
    const pollingPromise = pollApprovalEvents();

    try {
      await apiClient.approveRemediation(sessionId, input.approved, user, planVersion);
    } catch (error) {
      keepPollingApprovalEvents = false;
      void pollingPromise.catch(() => undefined);
      set({
        isApprovingPlan: false,
        error: formatApprovalErrorMessage(error, input.approved),
        approvalOverlayOpen: true,
      });
      throw error;
    }

    keepPollingApprovalEvents = false;
    void pollingPromise.catch(() => undefined);

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
      const canAdoptStreamSession =
        state.isStreamingDiagnosis &&
        isPendingSessionId(activeSessionId) &&
        Boolean(targetSessionId) &&
        !isPendingSessionId(targetSessionId);

      if (activeSessionId && targetSessionId && targetSessionId !== activeSessionId && !canAdoptStreamSession) {
        return state;
      }
      const resolvedSessionId = canAdoptStreamSession ? targetSessionId : activeSessionId ?? targetSessionId;

      const currentTrace = state.session?.trace?.steps ?? [];
      const nextEntries = [toThinkingStep(event, currentTrace.length + 1), toObservation(event)].filter(
        (entry): entry is ThinkingStep | Observation => Boolean(entry),
      );
      let nextSession = appendTraceEntries(state.session, nextEntries);
      if (canAdoptStreamSession && nextSession) {
        nextSession = {
          ...nextSession,
          session_id: resolvedSessionId ?? nextSession.session_id,
        };
      }
      let nextAlertSnapshot = state.alertSnapshot;
      let nextTopologyContext = state.topologyContext;
      let nextCompletedThinkingRounds = state.completedThinkingRounds;
      let nextLiveThinking = state.liveThinking;
      let nextLiveFinalAnswer = state.liveFinalAnswer;
      let nextActiveStreamingTools = state.activeStreamingTools;
      let nextStreamingPhase = state.streamingPhase;
      let nextRoundSequenceCounter = state.roundSequenceCounter;
      const data = isRecord(event.data) ? event.data : {};
      const eventSource = normalizeNonEmptyString(data._stream_source);
      const thoughtKey = getThoughtKeyFromData(data);
      const runId = normalizeNonEmptyString(data.run_id);
      const node = normalizeNonEmptyString(data.node);
      const roundId = getRoundIdFromData(data);

      if (event.type === "diagnosis_started") {
        nextAlertSnapshot = (data.alert as DiagnosisStartedData["alert"]) ?? null;
        nextTopologyContext = (data.topology as DiagnosisStartedData["topology"]) ?? null;
        nextStreamingPhase = "waiting_first_content";
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
            session: nextSession,
            activeSessionId: resolvedSessionId,
            isStreamingDiagnosis: true,
            streamingPhase: nextStreamingPhase,
            roundSequenceCounter: nextRoundSequenceCounter,
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
            session: nextSession,
            activeSessionId: resolvedSessionId,
            liveFinalAnswer: nextLiveFinalAnswer,
            isStreamingDiagnosis: true,
            streamingPhase: "streaming_final",
            roundSequenceCounter: nextRoundSequenceCounter,
          };
        }

        const resolvedThoughtKey = thoughtKey ?? nextLiveThinking?.thought_key ?? buildThoughtKey(runId, node);
        if (!resolvedThoughtKey) {
          return {
            ...state,
            session: nextSession,
            activeSessionId: resolvedSessionId,
            isStreamingDiagnosis: true,
            streamingPhase: nextStreamingPhase,
            roundSequenceCounter: nextRoundSequenceCounter,
          };
        }

        const isSameRound = matchesLiveThinkingRound(
          nextLiveThinking,
          {
            roundId,
            thoughtKey,
            runId,
            node,
          },
          true,
        );
        const canAppendToLiveRound = Boolean(nextLiveThinking && nextLiveThinking.status === "thinking" && isSameRound);
        if (!canAppendToLiveRound && nextLiveThinking) {
          const finalizedPreviousRound =
            nextLiveThinking.status === "thinking"
              ? completeLiveThinking(nextLiveThinking, "思考完成。")
              : nextLiveThinking;
          nextCompletedThinkingRounds = appendCompletedThinkingRound(
            nextCompletedThinkingRounds,
            finalizedPreviousRound,
          );
        }
        const roundTimestamp = normalizeNonEmptyString(data.started_at) ?? event.timestamp;
        if (!canAppendToLiveRound) {
          nextRoundSequenceCounter += 1;
        }
        const resolvedRoundSeq =
          canAppendToLiveRound
            ? nextLiveThinking?.round_seq ?? null
            : nextRoundSequenceCounter;
        const resolvedRoundId =
          roundId ??
          (canAppendToLiveRound
            ? nextLiveThinking?.round_id ??
              buildFallbackRoundId({
                thoughtKey: resolvedThoughtKey,
                runId,
                node,
                timestamp: roundTimestamp,
              })
            : buildFallbackRoundId({
                thoughtKey: resolvedThoughtKey,
                runId,
                node,
                timestamp: roundTimestamp,
              }));
        const previousContent = canAppendToLiveRound ? nextLiveThinking?.content ?? "" : "";
        nextActiveStreamingTools = canAppendToLiveRound ? nextActiveStreamingTools : [];
        nextLiveThinking = {
          round_id: resolvedRoundId,
          round_seq: resolvedRoundSeq,
          thought_key: resolvedThoughtKey,
          run_id: runId ?? (canAppendToLiveRound ? nextLiveThinking?.run_id ?? null : null),
          node: node ?? (canAppendToLiveRound ? nextLiveThinking?.node ?? null : null),
          timestamp: canAppendToLiveRound ? nextLiveThinking?.timestamp ?? roundTimestamp : roundTimestamp,
          content: `${previousContent}${content}`,
          status: "thinking",
          stream_seq: (canAppendToLiveRound ? nextLiveThinking?.stream_seq ?? 0 : 0) + 1,
          thought_duration_sec: null,
          next_action: canAppendToLiveRound ? nextLiveThinking?.next_action ?? null : null,
          tool_name: canAppendToLiveRound ? nextLiveThinking?.tool_name ?? null : null,
          active_tools: nextActiveStreamingTools,
        };
        return {
          ...state,
          session: nextSession,
          activeSessionId: resolvedSessionId,
          isStreamingDiagnosis: true,
          streamingPhase: "streaming_thought",
          completedThinkingRounds: nextCompletedThinkingRounds,
          roundSequenceCounter: nextRoundSequenceCounter,
          ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
        };
      }
      if (event.type === "node_started") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        if (nextLiveThinking) {
          const finalizedPreviousRound =
            nextLiveThinking.status === "thinking"
              ? completeLiveThinking(nextLiveThinking, "思考完成。")
              : nextLiveThinking;
          nextCompletedThinkingRounds = appendCompletedThinkingRound(
            nextCompletedThinkingRounds,
            finalizedPreviousRound,
          );
        }
        const resolvedThoughtKey = thoughtKey ?? buildThoughtKey(runId, node);
        const roundTimestamp = normalizeNonEmptyString(data.started_at) ?? event.timestamp;
        nextActiveStreamingTools = [];
        if (resolvedThoughtKey) {
          nextRoundSequenceCounter += 1;
        }
        nextLiveThinking = resolvedThoughtKey
          ? {
              round_id:
                buildFallbackRoundId({
                  explicitRoundId: roundId,
                  thoughtKey: resolvedThoughtKey,
                  runId,
                  node,
                  timestamp: roundTimestamp,
                }) ?? null,
              round_seq: nextRoundSequenceCounter,
              thought_key: resolvedThoughtKey,
              run_id: runId,
              node,
              timestamp: roundTimestamp,
              content: "",
              status: "thinking",
              stream_seq: 1,
              thought_duration_sec: null,
              next_action: null,
              tool_name: null,
              active_tools: nextActiveStreamingTools,
            }
          : null;
        return {
          ...state,
          session: nextSession,
          activeSessionId: resolvedSessionId,
          isStreamingDiagnosis: true,
          streamingPhase:
            state.streamingPhase === "bootstrapping" ? "waiting_first_content" : state.streamingPhase,
          completedThinkingRounds: nextCompletedThinkingRounds,
          roundSequenceCounter: nextRoundSequenceCounter,
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
        const existingTraceEntryKeys = new Set(currentTrace.map((entry) => buildTraceEntryDedupeKey(entry)));
        const snapshotEntries = snapshotItems
          .map((item, index) => toTraceEntryFromNodeSnapshot(item, currentTrace.length + index + 1))
          .filter((entry): entry is ThinkingStep | Observation => Boolean(entry))
          .filter((entry) => {
            const dedupeKey = buildTraceEntryDedupeKey(entry);
            if (existingTraceEntryKeys.has(dedupeKey)) {
              return false;
            }
            existingTraceEntryKeys.add(dedupeKey);
            return true;
          });
        const mergedEntries = [...nextEntries, ...snapshotEntries];
        nextSession = appendTraceEntries(nextSession, snapshotEntries);
        nextCompletedThinkingRounds = consumeCompletedRoundsBySnapshot(
          nextCompletedThinkingRounds,
          snapshotEntries,
        );
        const nextEvents = mergeSessionEvents(state.events, [event as SessionEvent]);
        const approvalState = deriveApprovalState(nextSession, nextEvents);
        const completionError = getEventError(event) ?? state.error;
        const nodeStatus = normalizeNonEmptyString(data.status)?.toLowerCase() ?? null;
        const completedThoughtKey = thoughtKey ?? nextLiveThinking?.thought_key ?? null;
        const liveMatchesCompletedRound = matchesLiveThinkingRound(
          nextLiveThinking,
          {
            roundId,
            thoughtKey: completedThoughtKey,
            runId,
            node,
          },
          false,
        );
        const snapshotHasCompletedThought = completedThoughtKey
          ? snapshotEntries.some(
              (entry) =>
                "thought" in entry &&
                typeof entry.thought_key === "string" &&
                entry.thought_key === completedThoughtKey &&
                (!nextLiveThinking ||
                  new Date(entry.timestamp).getTime() >= new Date(nextLiveThinking.timestamp).getTime()),
            )
          : false;
        if (liveMatchesCompletedRound && nextLiveThinking) {
          if (!snapshotHasCompletedThought) {
          nextLiveThinking = {
            ...nextLiveThinking,
            status: "completed",
            thought_duration_sec:
              typeof data.thought_duration_sec === "number"
                ? data.thought_duration_sec
                : nextLiveThinking.thought_duration_sec,
            stream_seq: (nextLiveThinking.stream_seq ?? 0) + 1,
            active_tools: [],
          };
          } else {
            nextLiveThinking = null;
          }
        }
        const terminalReason =
          normalizeTerminalReason(data.terminal_reason) ??
          (nodeStatus && TERMINAL_NODE_STATUSES.has(nodeStatus)
            ? resolveTerminalReasonFromStatus(nodeStatus)
            : null);
        const isTerminalNodeEvent = terminalReason !== null;
        const terminalPhase = isTerminalNodeEvent ? resolveTerminalPhase(terminalReason) : null;
        if (
          isTerminalNodeEvent &&
          nextLiveThinking &&
          (nextLiveThinking.status === "thinking" || nextLiveThinking.content.trim().length === 0)
        ) {
          nextLiveThinking = completeLiveThinking(
            nextLiveThinking,
            buildTerminalThinkingSummary({
              reason: terminalReason,
              status: nodeStatus,
              summary: normalizeNonEmptyString(data.summary),
              error: completionError,
            }),
          );
        }

        return {
          session: nextSession,
          activeSessionId: resolvedSessionId,
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
          isStreamingDiagnosis: !isTerminalNodeEvent,
          streamingPhase:
            terminalPhase ?? (nextLiveFinalAnswer ? "streaming_final" : "streaming_thought"),
          completedThinkingRounds: nextCompletedThinkingRounds,
          roundSequenceCounter: nextRoundSequenceCounter,
          ...toStreamingFields(nextLiveThinking, []),
          streamingAbortController: isTerminalNodeEvent ? null : state.streamingAbortController,
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
        if (!nextLiveThinking) {
          return {
            ...state,
            session: nextSession,
            activeSessionId: resolvedSessionId,
          };
        }
        const toolRoundMatchesLive = matchesLiveThinkingRound(
          nextLiveThinking,
          {
            roundId,
            thoughtKey: resolvedThoughtKey,
            runId,
            node,
          },
          true,
        );
        if (!toolRoundMatchesLive) {
          return {
            ...state,
            session: nextSession,
            activeSessionId: resolvedSessionId,
          };
        }
        const resolvedRoundId =
          roundId ??
          nextLiveThinking.round_id ??
          buildFallbackRoundId({
            thoughtKey: resolvedThoughtKey,
            runId,
            node,
            timestamp: event.timestamp,
          });
        const nextTool: StreamingToolCall = {
          tool,
          params: isRecord(data.params) ? data.params : {},
          round_id: resolvedRoundId,
          round_seq: nextLiveThinking.round_seq,
          thought_key: resolvedThoughtKey,
          run_id: runId,
          node,
        };
        const toolExists = nextActiveStreamingTools.some(
          (item) =>
            item.tool === nextTool.tool &&
            (nextTool.round_id
              ? item.round_id === nextTool.round_id
              : item.thought_key === nextTool.thought_key),
        );
        nextActiveStreamingTools = toolExists ? nextActiveStreamingTools : [...nextActiveStreamingTools, nextTool];
        nextLiveThinking = {
          ...nextLiveThinking,
          round_id: nextLiveThinking.round_id ?? resolvedRoundId ?? null,
          tool_name: nextLiveThinking.tool_name ?? tool,
          active_tools: nextActiveStreamingTools,
        };
        return {
          ...state,
          session: nextSession,
          activeSessionId: resolvedSessionId,
          isStreamingDiagnosis: true,
          streamingPhase: state.streamingPhase === "bootstrapping" ? "waiting_first_content" : state.streamingPhase,
          roundSequenceCounter: nextRoundSequenceCounter,
          ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
        };
      }
      if (event.type === "tool_completed") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        const tool = String(data.tool ?? "");
        if (!nextLiveThinking) {
          return {
            ...state,
            session: nextSession,
            activeSessionId: resolvedSessionId,
          };
        }
        const resolvedThoughtKey = thoughtKey ?? nextLiveThinking?.thought_key ?? buildThoughtKey(runId, node);
        const toolRoundMatchesLive = matchesLiveThinkingRound(
          nextLiveThinking,
          {
            roundId,
            thoughtKey: resolvedThoughtKey,
            runId,
            node,
          },
          true,
        );
        if (!toolRoundMatchesLive) {
          return {
            ...state,
            session: nextSession,
            activeSessionId: resolvedSessionId,
          };
        }
        const resolvedRoundId =
          roundId ??
          nextLiveThinking.round_id ??
          buildFallbackRoundId({
            thoughtKey: resolvedThoughtKey,
            runId,
            node,
            timestamp: event.timestamp,
          });
        nextActiveStreamingTools = nextActiveStreamingTools.filter((item) => {
          if (item.tool !== tool) {
            return true;
          }
          if (resolvedRoundId && item.round_id && item.round_id !== resolvedRoundId) {
            return true;
          }
          if (!resolvedRoundId && resolvedThoughtKey && item.thought_key && item.thought_key !== resolvedThoughtKey) {
            return true;
          }
          return false;
        });
        nextLiveThinking = {
          ...nextLiveThinking,
          active_tools: nextActiveStreamingTools,
        };
        return {
          ...state,
          session: nextSession,
          activeSessionId: resolvedSessionId,
          roundSequenceCounter: nextRoundSequenceCounter,
          ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
        };
      }
      if (event.type === "done") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        const doneStatus = normalizeNonEmptyString(data.status)?.toLowerCase() ?? null;
        const terminalReason =
          normalizeTerminalReason(data.terminal_reason) ?? resolveTerminalReasonFromStatus(doneStatus) ?? "done";
        const terminalPhase = resolveTerminalPhase(terminalReason);
        const completionError =
          terminalPhase === "error"
            ? normalizeNonEmptyString(data.summary) ?? state.error
            : state.error;
        const finalizedLiveThinking =
          state.liveThinking &&
          (state.liveThinking.status === "thinking" || state.liveThinking.content.trim().length === 0)
            ? completeLiveThinking(
                state.liveThinking,
                buildTerminalThinkingSummary({
                  reason: terminalReason,
                  status: doneStatus,
                  summary: normalizeNonEmptyString(data.summary),
                  error: completionError,
                }),
              )
            : state.liveThinking;
        return {
          ...state,
          session: nextSession,
          activeSessionId: resolvedSessionId,
          isStreamingDiagnosis: false,
          streamingPhase: terminalPhase,
          liveFinalAnswer: state.liveFinalAnswer
            ? { ...state.liveFinalAnswer, status: "completed" }
            : state.liveFinalAnswer,
          ...toStreamingFields(finalizedLiveThinking, []),
          streamingAbortController: null,
          error: completionError,
          roundSequenceCounter: nextRoundSequenceCounter,
        };
      }
      if (event.type === "error") {
        if (state.streamingAbortController && eventSource !== "sse") {
          return state;
        }
        const errorMessage = getEventError(event) ?? state.error ?? "streaming diagnosis failed";
        const finalizedLiveThinking =
          state.liveThinking &&
          (state.liveThinking.status === "thinking" || state.liveThinking.content.trim().length === 0)
            ? completeLiveThinking(
                state.liveThinking,
                buildTerminalThinkingSummary({
                  reason: "error",
                  error: errorMessage,
                }),
              )
            : state.liveThinking;
        return {
          ...state,
          session: nextSession,
          activeSessionId: resolvedSessionId,
          isStreamingDiagnosis: false,
          streamingPhase: "error",
          ...toStreamingFields(finalizedLiveThinking, []),
          streamingAbortController: null,
          liveFinalAnswer: state.liveFinalAnswer
            ? { ...state.liveFinalAnswer, status: "completed" }
            : state.liveFinalAnswer,
          error: errorMessage,
          roundSequenceCounter: nextRoundSequenceCounter,
        };
      }

      if (event.type === "diagnosis_result") {
        if (nextSession) {
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
        const nextEvents = mergeSessionEvents(state.events, [event as SessionEvent]);
        const approvalState = deriveApprovalState(nextSession, nextEvents);
        const finalizedLiveThinking =
          nextLiveThinking &&
          (nextLiveThinking.status === "thinking" || nextLiveThinking.content.trim().length === 0)
            ? completeLiveThinking(
                nextLiveThinking,
                buildTerminalThinkingSummary({
                  reason: "diagnosis_result",
                }),
              )
            : nextLiveThinking;
        return {
          session: nextSession,
          activeSessionId: resolvedSessionId,
          events: nextEvents,
          localAuditRecords: state.localAuditRecords,
          ...approvalState,
          approvalOverlayOpen:
            nextSession?.status === "approval_required" ? state.approvalOverlayOpen : false,
          effectiveReviseInstruction: state.effectiveReviseInstruction,
          messages: mergeEventMessages(state.messages, [event], resolvedSessionId),
          alertSnapshot: nextAlertSnapshot,
          topologyContext: nextTopologyContext,
          liveFinalAnswer: nextLiveFinalAnswer
            ? { ...nextLiveFinalAnswer, status: "completed" }
            : nextLiveFinalAnswer,
          ...toStreamingFields(finalizedLiveThinking, []),
          error: state.error,
          isStreamingDiagnosis: false,
          streamingPhase: "completed",
          traceStatus:
            nextEntries.length > 0 || event.type === "diagnosis_result"
              ? "ready"
              : state.traceStatus,
          streamingAbortController: null,
          roundSequenceCounter: nextRoundSequenceCounter,
        };
      }
      if (event.type === "diagnosis_candidates_ready") {
        if (nextSession) {
          const payload = isRecord(event.data) ? event.data : {};
          nextSession = {
            ...nextSession,
            diagnosis_result: buildDiagnosisResultFromCandidatesPayload(payload, nextSession.diagnosis_result),
            status: nextSession.status === "diagnosing" ? "diagnosed" : nextSession.status,
          };
        }
        const nextEvents = mergeSessionEvents(state.events, [event as SessionEvent]);
        const approvalState = deriveApprovalState(nextSession, nextEvents);
        return {
          session: nextSession,
          activeSessionId: resolvedSessionId,
          events: nextEvents,
          localAuditRecords: state.localAuditRecords,
          ...approvalState,
          approvalOverlayOpen:
            nextSession?.status === "approval_required" ? state.approvalOverlayOpen : false,
          effectiveReviseInstruction: state.effectiveReviseInstruction,
          messages: mergeEventMessages(state.messages, [event], resolvedSessionId),
          alertSnapshot: nextAlertSnapshot,
          topologyContext: nextTopologyContext,
          liveFinalAnswer: nextLiveFinalAnswer,
          ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
          error: state.error,
          streamingPhase: nextStreamingPhase,
          roundSequenceCounter: nextRoundSequenceCounter,
          traceStatus:
            nextEntries.length > 0
              ? "ready"
              : state.traceStatus,
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
        if (["execution_started", "canary_started", "canary_progress", "canary_batch_progress", "full_rollout_started", "full_rollout_progress"].includes(stage)) {
          nextSession = {
            ...nextSession,
            status: stage.includes("canary") ? "validating" : "remediating",
          };
        }
        if (["canary_succeeded", "canary_completed", "observation_started", "observation_result"].includes(stage)) {
          nextSession = {
            ...nextSession,
            status: "validating",
          };
        }
        if (["execution_succeeded", "full_rollout_succeeded", "alert_recovered"].includes(stage)) {
          nextSession = {
            ...nextSession,
            status: "resolved",
          };
        }
        if (stage === "session_closed") {
          nextSession = {
            ...nextSession,
            status: "closed",
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
        activeSessionId: resolvedSessionId,
        events: nextEvents,
        localAuditRecords: state.localAuditRecords,
        ...approvalState,
        approvalOverlayOpen,
        effectiveReviseInstruction: state.effectiveReviseInstruction,
        messages: mergeEventMessages(state.messages, [event], resolvedSessionId),
        alertSnapshot: nextAlertSnapshot,
        topologyContext: nextTopologyContext,
        liveFinalAnswer: nextLiveFinalAnswer,
        ...toStreamingFields(nextLiveThinking, nextActiveStreamingTools),
        error: getEventError(event) ?? state.error,
        streamingPhase: nextStreamingPhase,
        roundSequenceCounter: nextRoundSequenceCounter,
        traceStatus:
          nextEntries.length > 0
            ? "ready"
            : state.traceStatus,
      };
    });

    const backfillSessionId =
      targetSessionId && !isPendingSessionId(targetSessionId) ? targetSessionId : get().activeSessionId;
    const backfillSessionStatus = get().session?.status?.trim().toLowerCase() ?? "";
    const isTerminalBackfill = TERMINAL_SESSION_STATUSES.has(backfillSessionStatus);
    if (backfillSessionId && !isPendingSessionId(backfillSessionId) && shouldTriggerSessionBackfill(event) && !isTerminalBackfill) {
      scheduleSessionBackfill(backfillSessionId);
    }
  },
  startStreamingDiagnosis: (alert, extraAlertFingerprints = [], onSessionReady) => {
    const controller = new AbortController();
    const pendingSessionId = `${PENDING_SESSION_PREFIX}${Date.now()}`;
    const bootstrapThinkingKey = `bootstrap:${pendingSessionId}`;
    const bootstrapTimestamp = new Date().toISOString();
    set({
      session: buildPendingSession(alert, pendingSessionId),
      activeSessionId: pendingSessionId,
      bootstrapStatus: "ready",
      traceStatus: "empty",
      alertSnapshot: {
        alert_name: alert.alert_name,
        severity: alert.severity,
        labels: alert.labels ?? {},
        annotations: alert.annotations ?? {},
        fingerprint: alert.fingerprint,
        summary: alert.summary ?? undefined,
        description: alert.description ?? undefined,
        source: alert.source ?? undefined,
        status: alert.status,
      },
      topologyContext: null,
      events: [],
      messages: [],
      localAuditRecords: [],
      approvalOverlayOpen: false,
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
      completedThinkingRounds: [],
      liveThinking: {
        round_id: `${bootstrapThinkingKey}@${bootstrapTimestamp}`,
        round_seq: 0,
        thought_key: bootstrapThinkingKey,
        run_id: null,
        node: "bootstrap",
        timestamp: bootstrapTimestamp,
        content: "",
        status: "thinking",
        stream_seq: 1,
        thought_duration_sec: null,
        next_action: null,
        tool_name: null,
        active_tools: [],
      },
      liveFinalAnswer: null,
      streamingText: "",
      streamingNode: "bootstrap",
      isStreamingDiagnosis: true,
      streamingPhase: "bootstrapping",
      streamSequenceCounter: 0,
      roundSequenceCounter: 0,
      activeStreamingTools: [],
      streamingAbortController: controller,
      error: undefined,
    });

    const run = async () => {
      let sessionFired = false;
      let streamSeq = 0;
      try {
        await streamDiagnosis(
          alert,
          extraAlertFingerprints,
          (event) => {
            const data = isRecord(event.data) ? event.data : {};
            streamSeq += 1;
            const timestamp =
              typeof event.timestamp === "string" && event.timestamp.trim().length > 0
                ? event.timestamp
                : new Date().toISOString();
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
                "diagnosis_candidates_ready",
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
                  _stream_seq: streamSeq,
                },
              });
            }
          },
          controller.signal,
        );
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          const errorMessage = error instanceof Error ? error.message : "streaming diagnosis failed";
          set((state) => {
            const finalizedLiveThinking =
              state.liveThinking &&
              (state.liveThinking.status === "thinking" || state.liveThinking.content.trim().length === 0)
                ? completeLiveThinking(
                    state.liveThinking,
                    buildTerminalThinkingSummary({
                      reason: "error",
                      error: errorMessage,
                    }),
                  )
                : state.liveThinking;
            return {
              isStreamingDiagnosis: false,
              liveFinalAnswer: state.liveFinalAnswer
                ? { ...state.liveFinalAnswer, status: "completed" }
                : state.liveFinalAnswer,
              ...toStreamingFields(finalizedLiveThinking, []),
              streamingPhase: "error",
              streamingAbortController: null,
              error: errorMessage,
              roundSequenceCounter: state.roundSequenceCounter,
              activeStreamingTools: [],
            };
          });
        }
      }
    };
    void run();
    return pendingSessionId;
  },
  cancelStreamingDiagnosis: () => {
    const controller = get().streamingAbortController;
    controller?.abort();
    set({
      isStreamingDiagnosis: false,
      completedThinkingRounds: [],
      liveThinking: null,
      liveFinalAnswer: null,
      streamingText: "",
      streamingNode: null,
      activeStreamingTools: [],
      streamingAbortController: null,
      streamingPhase: "idle",
      streamSequenceCounter: 0,
      roundSequenceCounter: 0,
    });
  },
}));
