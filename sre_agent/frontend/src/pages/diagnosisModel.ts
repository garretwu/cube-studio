import type { ChatMessage, DiagnosisLocalAuditRecord, DiagnosisResult, DiagnosisSession, Observation, SessionEvent, ThinkingStep } from "../api/types";
import { formatDateTime, formatDateTimeParts } from "../utils/format";

type ChipTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

type TimelineToolStatus = "loading" | "success" | "error" | "timeout";

type TimelineSortItem = {
  order: number;
  timestamp: string;
  item: DiagnosisTimelineItem;
};

export type DiagnosisSystemEventView = {
  id: string;
  kind: "system";
  eventKind: "approval_result" | "execution_progress";
  summary: string;
  details: string[];
  timestamp: string;
  statusTone: ChipTone;
  source: "optimistic" | "event" | "local_audit";
  dedupeKey: string;
};

export type DiagnosisTimelineItem =
  | {
      id: string;
      kind: "message";
      role: "user" | "assistant";
      content: string;
      timestamp: string;
      label?: string;
    }
  | {
      id: string;
      kind: "thinking";
      title: string;
      content: string;
      timestamp: string;
      toolName?: string | null;
      status: "thinking" | "completed";
      thoughtDurationSec?: number;
    }
  | DiagnosisSystemEventView
  | {
      id: string;
      kind: "tool";
      toolName: string;
      params: Record<string, unknown>;
      timestamp: string;
      status: TimelineToolStatus;
      summaryLines: string[];
      rawResult?: Record<string, unknown> | null;
    };

export type DiagnosisCandidateView = {
  id: string;
  title: string;
  summary: string;
  confidence: number;
  confidenceLabel: string;
  statusLabel: string;
  statusTone: ChipTone;
  evidenceFor: string[];
  evidenceAgainst: string[];
  layer?: string;
  entities: string[];
  rank?: number;
  evidenceSummary?: string;
  distinguishingVerification?: string | null;
  isPrimary: boolean;
};

export type DiagnosisHypothesisView = {
  id: string;
  description: string;
  statusLabel: string;
  statusTone: ChipTone;
  evidenceForCount: number;
  evidenceAgainstCount: number;
  confidence: number;
};

export type DiagnosisPropagationStepView = {
  id: string;
  entityId: string;
  entityType: string;
  metric: string;
  valueBefore: number | string;
  valueAfter: number | string;
  description: string;
};

export type DiagnosisSummaryView = {
  title: string;
  subtitle: string;
  certaintyLabel: string;
  certaintyTone: ChipTone;
  confidenceLabel: string;
  confidenceRawLabel?: string;
  priorityLabel?: string;
  sessionLabel?: string;
  updatedTimeLabel?: string;
  updatedDateTimeLabel?: string;
  affectedServices: string[];
  impactSummary: string;
  rootCause?: string;
  rootCauseLayer?: string;
  rootCauseLayerLabel?: string;
  rootCauseEntities?: string[];
};

export type DiagnosisPlanStepView = {
  id: string;
  title: string;
  detail: string;
  toolName?: string;
  paramsSummary?: string;
  status: "done" | "pending";
};

export type DiagnosisPlanView = {
  title: string;
  description: string;
  priorityLabel: string;
  confidenceLabel: string;
  safetyLabel?: string;
  canaryLabel?: string;
  steps: DiagnosisPlanStepView[];
};

export type DiagnosisLiveView = {
  timeline: DiagnosisTimelineItem[];
  candidates: DiagnosisCandidateView[];
  hypotheses?: DiagnosisHypothesisView[];
  propagationChain?: DiagnosisPropagationStepView[];
  summary?: DiagnosisSummaryView;
  plan?: DiagnosisPlanView;
};

export type DiagnosisDemoEvent =
  | {
      delayMs: number;
      type: "append";
      item: DiagnosisTimelineItem;
    }
  | {
      delayMs: number;
      type: "update_tool";
      targetId: string;
      summaryLines: string[];
      rawResult?: Record<string, unknown>;
    }
  | {
      delayMs: number;
      type: "update_thinking";
      targetId: string;
      status: "thinking" | "completed";
      thoughtDurationSec?: number;
    }
  | {
      delayMs: number;
      type: "complete";
    };

export type DiagnosisDemoScenario = {
  initialTimeline: DiagnosisTimelineItem[];
  events: DiagnosisDemoEvent[];
  candidates: DiagnosisCandidateView[];
  hypotheses?: DiagnosisHypothesisView[];
  propagationChain?: DiagnosisPropagationStepView[];
  summary: DiagnosisSummaryView;
  plan: DiagnosisPlanView;
};

function isThinkingStep(entry: ThinkingStep | Observation): entry is ThinkingStep {
  return "thought" in entry;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

const UNICODE_ESCAPE_PATTERN = /\\u[0-9a-fA-F]{4}/;
const LATIN_MOJIBAKE_PATTERN = /[\u00C0-\u00FF]/;
const CJK_PATTERN = /[\u3400-\u9FFF]/g;
const REPLACEMENT_CHAR_PATTERN = /\uFFFD/g;

function countMatches(text: string, pattern: RegExp) {
  const matched = text.match(pattern);
  return matched ? matched.length : 0;
}

function getTextQualityScore(text: string) {
  const cjkCount = countMatches(text, CJK_PATTERN);
  const replacementCount = countMatches(text, REPLACEMENT_CHAR_PATTERN);
  const latinSupplementCount = countMatches(text, /[\u00C0-\u00FF]/g);

  return cjkCount * 4 - replacementCount * 8 - latinSupplementCount * 2;
}

function decodeUnicodeEscapes(text: string) {
  if (!UNICODE_ESCAPE_PATTERN.test(text)) {
    return text;
  }

  try {
    const escaped = text
      .replace(/\\/g, "\\\\")
      .replace(/\\\\u/g, "\\u")
      .replace(/"/g, '\\"')
      .replace(/\r/g, "\\r")
      .replace(/\n/g, "\\n");
    return JSON.parse(`"${escaped}"`) as string;
  } catch {
    return text;
  }
}

function repairUtf8Mojibake(text: string) {
  if (!LATIN_MOJIBAKE_PATTERN.test(text)) {
    return text;
  }

  const chars = Array.from(text);
  const bytes = chars.map((char) => char.charCodeAt(0));
  if (bytes.some((code) => code > 0xff)) {
    return text;
  }

  try {
    const repaired = new TextDecoder("utf-8").decode(Uint8Array.from(bytes));
    return getTextQualityScore(repaired) > getTextQualityScore(text) ? repaired : text;
  } catch {
    return text;
  }
}

export function normalizeDiagnosisDisplayText(text: string) {
  let current = text;

  for (let index = 0; index < 3; index += 1) {
    const decodedUnicode = decodeUnicodeEscapes(current);
    const repairedMojibake = repairUtf8Mojibake(decodedUnicode);
    if (repairedMojibake === current) {
      break;
    }
    current = repairedMojibake;
  }

  return current;
}

function normalizeStringList(values: string[] | undefined) {
  return (values ?? []).map((value) => normalizeDiagnosisDisplayText(value));
}

function formatValue(value: unknown): string {
  if (value == null) {
    return "-";
  }

  if (typeof value === "string") {
    const normalized = normalizeDiagnosisDisplayText(value);
    return normalized.length > 72 ? `${normalized.slice(0, 69)}...` : normalized;
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  if (Array.isArray(value)) {
    return value.slice(0, 3).map((item) => formatValue(item)).join(", ");
  }

  return JSON.stringify(value);
}

function summarizeResult(result: Record<string, unknown> | undefined): string[] {
  if (!result) {
    return ["Waiting for tool result..."];
  }

  const entries = Object.entries(result)
    .filter(([, value]) => value !== undefined && value !== null)
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${formatValue(value)}`);

  return entries.length > 0 ? entries : ["Tool finished, but no displayable summary was returned."];
}

function normalizeConfidence(value: number | undefined) {
  return Math.max(0, Math.min(1, value ?? 0));
}

function getConfidenceLabel(value: number | undefined) {
  return `${Math.round(normalizeConfidence(value) * 100)}%`;
}

function getConfidenceRawLabel(value: number | undefined) {
  return normalizeConfidence(value).toFixed(2);
}

function getUpdatedLabelParts(timestamp: string | undefined) {
  if (!timestamp) {
    return {
      updatedTimeLabel: "--",
      updatedDateTimeLabel: "--",
    };
  }

  const { date, time } = formatDateTimeParts(timestamp);
  const safeDate = date || "--";
  const safeTime = time || safeDate;

  return {
    updatedTimeLabel: safeTime,
    updatedDateTimeLabel: time ? `${safeDate} ${time}` : safeDate,
  };
}

function getLatestTraceTimestamp(session: DiagnosisSession | undefined) {
  const traceSteps = session?.trace?.steps ?? [];
  for (let index = traceSteps.length - 1; index >= 0; index -= 1) {
    const step = traceSteps[index];
    if (step?.timestamp) {
      return step.timestamp;
    }
  }
  return undefined;
}

function resolveSummaryTimestamp(
  summary: DiagnosisSummaryView | undefined,
  timestamp: string | undefined,
): DiagnosisSummaryView | undefined {
  if (!summary) {
    return summary;
  }

  return {
    ...summary,
    ...getUpdatedLabelParts(timestamp),
  };
}

function getLayerLabel(value: string | undefined) {
  switch (value) {
    case "hardware":
      return "\u786c\u4ef6";
    case "network":
      return "\u7f51\u7edc";
    case "os":
      return "\u64cd\u4f5c\u7cfb\u7edf";
    case "platform":
      return "\u5e73\u53f0";
    case "service":
      return "\u670d\u52a1";
    default:
      return value || "\u672a\u77e5";
  }
}

function getCertaintyTone(value: DiagnosisResult["diagnosis_certainty"] | undefined): ChipTone {
  switch (value) {
    case "confirmed":
      return "success";
    case "probable":
      return "accent";
    case "ambiguous":
      return "warning";
    default:
      return "neutral";
  }
}

function getCertaintyLabel(value: DiagnosisResult["diagnosis_certainty"] | undefined) {
  switch (value) {
    case "confirmed":
      return "\u5df2\u786e\u8ba4";
    case "probable":
      return "\u9ad8\u6982\u7387";
    case "ambiguous":
      return "\u5f85\u786e\u8ba4";
    default:
      return "\u5f85\u786e\u8ba4";
  }
}

function getHypothesisTone(status: DiagnosisResult["hypotheses"][number]["status"]): ChipTone {
  switch (status) {
    case "confirmed":
      return "success";
    case "testing":
      return "warning";
    case "eliminated":
      return "neutral";
    default:
      return "neutral";
  }
}

function getHypothesisLabel(status: DiagnosisResult["hypotheses"][number]["status"]) {
  switch (status) {
    case "confirmed":
      return "\u5df2\u786e\u8ba4";
    case "testing":
      return "\u9a8c\u8bc1\u4e2d";
    case "eliminated":
      return "\u5df2\u6392\u9664";
    default:
      return "\u5019\u9009";
  }
}

function formatParamsSummary(params: Record<string, unknown>) {
  const parts = Object.entries(params)
    .slice(0, 3)
    .map(([key, value]) => `${key}=${formatValue(value)}`);
  return parts.join(" | ");
}

function buildSummary(session: DiagnosisSession | undefined): DiagnosisSummaryView | undefined {
  const result = session?.diagnosis_result;
  if (!result) {
    return undefined;
  }

  const updatedLabels = getUpdatedLabelParts(getLatestTraceTimestamp(session));

  return {
    title: "\u6839\u56e0\u8bca\u65ad",
    subtitle: "\u57fa\u4e8e\u73b0\u6709\u8bc1\u636e\u6574\u7406\u51fa\u7684\u5f53\u524d\u7ed3\u8bba\u3002",
    certaintyLabel: getCertaintyLabel(result.diagnosis_certainty),
    certaintyTone: getCertaintyTone(result.diagnosis_certainty),
    confidenceLabel: getConfidenceLabel(result.confidence),
    confidenceRawLabel: getConfidenceRawLabel(result.confidence),
    priorityLabel: result.triage_priority,
    sessionLabel: session?.session_id,
    updatedTimeLabel: updatedLabels.updatedTimeLabel,
    updatedDateTimeLabel: updatedLabels.updatedDateTimeLabel,
    affectedServices: normalizeStringList(result.affected_services),
    impactSummary: normalizeDiagnosisDisplayText(result.impact_summary),
    rootCause: normalizeDiagnosisDisplayText(result.root_cause),
    rootCauseLayer: result.root_cause_layer,
    rootCauseLayerLabel: getLayerLabel(result.root_cause_layer),
    rootCauseEntities: normalizeStringList(result.root_cause_entities),
  };
}

function buildCandidates(session: DiagnosisSession | undefined): DiagnosisCandidateView[] {
  const result = session?.diagnosis_result;
  if (!result) {
    return [];
  }

  const hypotheses = result.hypotheses ?? [];
  const rankedCandidates = [...(result.ranked_candidates ?? [])].sort((left, right) => left.rank - right.rank);

  if (rankedCandidates.length > 0) {
    return rankedCandidates.map((candidate, index) => {
      const matchedHypothesis = hypotheses.find(
        (item) => item.description.trim().toLowerCase() === candidate.root_cause.trim().toLowerCase(),
      );
      const isPrimary = candidate.rank === 1 || index === 0;

      return {
        id: `ranked-${candidate.rank}-${candidate.root_cause}`,
        title: normalizeDiagnosisDisplayText(candidate.root_cause),
        summary: normalizeDiagnosisDisplayText(candidate.evidence_summary),
        confidence: candidate.confidence,
        confidenceLabel: getConfidenceLabel(candidate.confidence),
        statusLabel: isPrimary
          ? getCertaintyLabel(result.diagnosis_certainty)
          : matchedHypothesis
            ? getHypothesisLabel(matchedHypothesis.status)
            : "\u5019\u9009",
        statusTone: isPrimary
          ? getCertaintyTone(result.diagnosis_certainty)
          : matchedHypothesis
            ? getHypothesisTone(matchedHypothesis.status)
            : "neutral",
        evidenceFor: normalizeStringList(matchedHypothesis?.evidence_for?.slice(0, 3) ?? [candidate.evidence_summary]),
        evidenceAgainst: normalizeStringList(matchedHypothesis?.evidence_against?.slice(0, 2) ?? []),
        layer: candidate.root_cause_layer,
        entities: normalizeStringList(candidate.root_cause_entities),
        rank: candidate.rank,
        evidenceSummary: normalizeDiagnosisDisplayText(candidate.evidence_summary),
        distinguishingVerification: candidate.distinguishing_verification
          ? normalizeDiagnosisDisplayText(candidate.distinguishing_verification)
          : candidate.distinguishing_verification,
        isPrimary,
      };
    });
  }

  const items: DiagnosisCandidateView[] = [];
  const normalizedRootCause = normalizeDiagnosisDisplayText(result.root_cause).trim();

  if (normalizedRootCause) {
    items.push({
      id: `primary-${normalizedRootCause}`,
      title: normalizedRootCause,
      summary: "\u5b9e\u65f6\u8bc1\u636e\u4e0e\u5386\u53f2\u7279\u5f81\u5f3a\u4e00\u81f4\uff0c\u5f53\u524d\u4f18\u5148\u7ea7\u6700\u9ad8\u3002",
      confidence: result.confidence,
      confidenceLabel: getConfidenceLabel(result.confidence),
      statusLabel: getCertaintyLabel(result.diagnosis_certainty),
      statusTone: getCertaintyTone(result.diagnosis_certainty),
      evidenceFor: normalizeStringList(hypotheses[0]?.evidence_for?.slice(0, 3) ?? [result.impact_summary]),
      evidenceAgainst: normalizeStringList(hypotheses[0]?.evidence_against?.slice(0, 2) ?? []),
      layer: result.root_cause_layer,
      entities: normalizeStringList(result.root_cause_entities),
      rank: 1,
      evidenceSummary: normalizeDiagnosisDisplayText(hypotheses[0]?.evidence_for?.[0] ?? result.impact_summary),
      distinguishingVerification: null,
      isPrimary: true,
    });
  }

  hypotheses.forEach((hypothesis, index) => {
    const normalizedHypothesisDescription = normalizeDiagnosisDisplayText(hypothesis.description).trim();
    if (normalizedHypothesisDescription === normalizedRootCause) {
      return;
    }

    items.push({
      id: `hypothesis-${index + 1}`,
      title: normalizedHypothesisDescription,
      summary: hypothesis.status === "eliminated" ? "\u5f53\u524d\u8bc1\u636e\u4e0d\u652f\u6301\u8be5\u8def\u5f84\u3002" : "\u8be5\u8def\u5f84\u4ecd\u5728\u5019\u9009\u8303\u56f4\u5185\u3002",
      confidence: hypothesis.confidence,
      confidenceLabel: getConfidenceLabel(hypothesis.confidence),
      statusLabel: getHypothesisLabel(hypothesis.status),
      statusTone: getHypothesisTone(hypothesis.status),
      evidenceFor: normalizeStringList(hypothesis.evidence_for.slice(0, 3)),
      evidenceAgainst: normalizeStringList(hypothesis.evidence_against.slice(0, 2)),
      layer: index === 0 ? result.root_cause_layer : undefined,
      entities: index === 0 ? normalizeStringList(result.root_cause_entities) : [],
      rank: index + 2,
      evidenceSummary: normalizeDiagnosisDisplayText(hypothesis.evidence_for[0] ?? "\u8bc1\u636e\u6458\u8981"),
      distinguishingVerification: null,
      isPrimary: false,
    });
  });

  return items;
}

function buildHypotheses(session: DiagnosisSession | undefined): DiagnosisHypothesisView[] {
  const hypotheses = session?.diagnosis_result?.hypotheses ?? [];
  return hypotheses.map((item, index) => ({
    id: `hypothesis-view-${index + 1}-${item.description}`,
    description: normalizeDiagnosisDisplayText(item.description),
    statusLabel: getHypothesisLabel(item.status),
    statusTone: getHypothesisTone(item.status),
    evidenceForCount: item.evidence_for.length,
    evidenceAgainstCount: item.evidence_against.length,
    confidence: item.confidence,
  }));
}

function buildPropagationChain(session: DiagnosisSession | undefined): DiagnosisPropagationStepView[] {
  const steps = session?.diagnosis_result?.propagation_chain ?? [];
  return steps.map((step, index) => ({
    id: `propagation-${index + 1}-${step.entity_id}`,
    entityId: normalizeDiagnosisDisplayText(step.entity_id),
    entityType: normalizeDiagnosisDisplayText(step.entity_type),
    metric: normalizeDiagnosisDisplayText(step.metric),
    valueBefore: step.value_before,
    valueAfter: step.value_after,
    description: normalizeDiagnosisDisplayText(step.description),
  }));
}

function buildPlan(session: DiagnosisSession | undefined): DiagnosisPlanView | undefined {
  const plan = session?.diagnosis_result?.recommended_fix;
  if (!plan) {
    return undefined;
  }

  const isResolved = ["resolved", "closed"].includes(session?.status ?? "");

  return {
    title: normalizeDiagnosisDisplayText(plan.root_cause),
    description: normalizeDiagnosisDisplayText(plan.description),
    priorityLabel: plan.priority,
    confidenceLabel: getConfidenceLabel(plan.confidence),
    safetyLabel: plan.safety_level,
    canaryLabel:
      plan.canary?.enabled ? `\u91d1\u4e1d\u96c0 ${plan.canary.target_percentage}% | \u89c2\u6d4b ${plan.canary.monitor_duration}m` : undefined,
    steps: plan.steps.map((step, index) => ({
      id: `${plan.plan_id}-${step.step_id}-${index}`,
      title: normalizeDiagnosisDisplayText(step.description),
      detail: `${step.tool} | \u8017\u65f6 ${step.timeout}s | \u6821\u9a8c ${step.verification.method}`,
      toolName: step.tool,
      paramsSummary: Object.keys(step.params).length > 0 ? formatParamsSummary(step.params) : undefined,
      status: isResolved && index === 0 ? "done" : "pending",
    })),
  };
}

function buildTraceNextAction(entry: ThinkingStep): string | undefined {
  if (typeof entry.next_action === "string" && entry.next_action.trim().length > 0) {
    return entry.next_action.trim();
  }
  return undefined;
}

function isSyntheticRemediationMessage(message: ChatMessage) {
  const eventType = String(message.metadata?.["event_type"] ?? "").trim().toLowerCase();
  return [
    "approval_required",
    "plan_revised",
    "remediation_progress",
    "execution_started",
    "execution_succeeded",
    "execution_failed",
    "execution_timeout",
  ].includes(eventType);
}

function buildPlanStepDetailLines(plan: DiagnosisSession["diagnosis_result"] | undefined) {
  const steps = plan?.recommended_fix?.steps ?? [];
  if (steps.length === 0) {
    return ["执行步骤：--"];
  }

  return steps.map((step, index) => `步骤 ${index + 1}：${normalizeDiagnosisDisplayText(step.description)}`);
}

function getSystemRecordPriority(source: DiagnosisLocalAuditRecord["source"]) {
  switch (source) {
    case "event":
      return 3;
    case "local_audit":
      return 2;
    case "optimistic":
    default:
      return 1;
  }
}

function resolveRecordUser(data: Record<string, unknown>) {
  const approver = typeof data.approver === "string" ? data.approver.trim() : "";
  if (approver) {
    return approver;
  }

  const user = typeof data.user === "string" ? data.user.trim() : "";
  return user || "alice";
}

function buildApprovalResultFromExecutionEvent(
  event: SessionEvent,
  session: DiagnosisSession | undefined,
): DiagnosisLocalAuditRecord | null {
  if (event.type !== "remediation_progress") {
    return null;
  }

  const data = isRecord(event.data) ? event.data : {};
  const stage = String(data.stage ?? "").trim().toLowerCase();
  if (stage !== "execution_started") {
    return null;
  }

  const plan = session?.diagnosis_result?.recommended_fix;
  const planVersion = normalizePlanVersion(data.plan_version) ?? parsePlanVersionFromPlanId(plan?.plan_id) ?? null;
  const versionLabel = planVersion ? `v${planVersion}` : "v?";
  const approver = resolveRecordUser(data);
  const details = [
    `审批时间：${formatDateTime(event.timestamp)}`,
    `方案版本：${planVersion ? `v${planVersion}` : "--"}`,
    `方案 ID：${plan?.plan_id ?? "--"}`,
    `审批人：${approver}`,
    "审批动作：同意，通过执行",
    `方案标题：${plan ? normalizeDiagnosisDisplayText(plan.root_cause) : "--"}`,
    ...buildPlanStepDetailLines(session?.diagnosis_result),
  ];

  return {
    id: `event-approval-approved-${event.timestamp}`,
    sessionId: event.session_id,
    eventKind: "approval_result",
    source: "event",
    dedupeKey: `approval-result-approved-${versionLabel}`,
    timestamp: event.timestamp,
    summary: `[系统] 已审批，通过执行（${versionLabel}，审批人 ${approver}）`,
    details,
    statusTone: "success",
  };
}

function getExecutionStageLabel(stage: string) {
  switch (stage) {
    case "execution_started":
      return "开始执行";
    case "execution_succeeded":
      return "执行成功";
    case "execution_failed":
      return "执行失败";
    case "execution_timeout":
      return "执行超时";
    default:
      return stage || "执行进度";
  }
}

function getExecutionStageTone(stage: string): ChipTone {
  switch (stage) {
    case "execution_started":
      return "warning";
    case "execution_succeeded":
      return "success";
    case "execution_failed":
    case "execution_timeout":
      return "danger";
    default:
      return "info";
  }
}

function buildExecutionProgressRecord(event: SessionEvent): DiagnosisLocalAuditRecord | null {
  if (event.type !== "remediation_progress") {
    return null;
  }

  const data = isRecord(event.data) ? event.data : {};
  const stage = String(data.stage ?? "").trim().toLowerCase();
  if (!["execution_started", "execution_succeeded", "execution_failed", "execution_timeout"].includes(stage)) {
    return null;
  }

  const stageLabel = getExecutionStageLabel(stage);
  const message = typeof data.message === "string" && data.message.trim()
    ? normalizeDiagnosisDisplayText(data.message)
    : "";
  const operator = resolveRecordUser(data);
  const details = [
    `状态时间：${formatDateTime(event.timestamp)}`,
    `执行阶段：${stageLabel}`,
    `执行人：${operator}`,
  ];

  if (message) {
    details.push(`执行说明：${message}`);
  }

  const timeoutSeconds = Number(data.timeout_seconds ?? 0);
  if (Number.isFinite(timeoutSeconds) && timeoutSeconds > 0) {
    details.push(`超时上限：${timeoutSeconds}s`);
  }

  const stepResults = Array.isArray(data.step_results) ? data.step_results : [];
  stepResults.slice(0, 3).forEach((step, index) => {
    if (!isRecord(step)) {
      return;
    }
    const tool = typeof step.tool === "string" ? step.tool : "step";
    const command = typeof step.command === "string" ? step.command : "";
    details.push(`细节 ${index + 1}：${tool}${command ? ` | ${normalizeDiagnosisDisplayText(command)}` : ""}`);
  });

  return {
    id: `event-${stage}-${event.timestamp}`,
    sessionId: event.session_id,
    eventKind: "execution_progress",
    source: "event",
    dedupeKey: `execution-progress-${stage}-${event.timestamp}`,
    timestamp: event.timestamp,
    summary: message ? `[系统] ${stageLabel}：${message}` : `[系统] ${stageLabel}`,
    details,
    statusTone: getExecutionStageTone(stage),
  };
}

function buildSystemRecords(
  session: DiagnosisSession | undefined,
  events: SessionEvent[],
  localAuditRecords: DiagnosisLocalAuditRecord[],
) {
  const eventDerived = events.flatMap((event) => {
    const items = [
      buildApprovalResultFromExecutionEvent(event, session),
      buildExecutionProgressRecord(event),
    ].filter((item): item is DiagnosisLocalAuditRecord => Boolean(item));
    return items;
  });

  const deduped = new Map<string, DiagnosisLocalAuditRecord>();
  [...localAuditRecords, ...eventDerived].forEach((record) => {
    const existing = deduped.get(record.dedupeKey);
    if (!existing || getSystemRecordPriority(record.source) >= getSystemRecordPriority(existing.source)) {
      deduped.set(record.dedupeKey, record);
    }
  });

  return [...deduped.values()].sort((left, right) => {
    return new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
  });
}

export function buildDiagnosisLiveView(
  session: DiagnosisSession | undefined,
  messages: ChatMessage[],
  events: SessionEvent[] = [],
  localAuditRecords: DiagnosisLocalAuditRecord[] = [],
): DiagnosisLiveView {
  const timelineItems: TimelineSortItem[] = [];
  const traceEntries = session?.trace?.steps ?? [];
  const pendingTools: Array<Extract<DiagnosisTimelineItem, { kind: "tool" }>> = [];

  for (let index = 0; index < traceEntries.length; index += 1) {
    const entry = traceEntries[index];
    if (!entry) {
      continue;
    }

    if (isThinkingStep(entry)) {
      timelineItems.push({
        order: timelineItems.length,
        timestamp: entry.timestamp,
        item: {
          id: `trace-thinking-${index + 1}-${entry.timestamp}`,
          kind: "thinking",
          title:
            entry.action_type === "tool_call"
              ? "Agent is planning a tool call"
              : entry.action_type === "conclude"
                ? "Agent is converging on the diagnosis"
                : "Agent is expanding diagnostic context",
          content: entry.thought,
          timestamp: entry.timestamp,
          toolName: entry.tool_name,
          status: "completed",
          thoughtDurationSec:
            typeof entry.thought_duration_sec === "number" && Number.isFinite(entry.thought_duration_sec)
              ? Math.max(1, Math.round(entry.thought_duration_sec))
              : undefined,
        },
      });

      const backendNextAction = buildTraceNextAction(entry);
      if (backendNextAction) {
        timelineItems.push({
          order: timelineItems.length,
          timestamp: entry.timestamp,
          item: {
            id: `trace-next-action-${index + 1}-${entry.timestamp}`,
            kind: "message",
            role: "assistant",
            content: backendNextAction,
            timestamp: entry.timestamp,
            label: "Next action",
          },
        });
      }

      if (entry.action_type === "tool_call" && entry.tool_name) {
        const toolItem: Extract<DiagnosisTimelineItem, { kind: "tool" }> = {
          id: `trace-tool-${index + 1}-${entry.timestamp}`,
          kind: "tool",
          toolName: entry.tool_name,
          params: entry.tool_params ?? {},
          timestamp: entry.timestamp,
          status: "loading",
          summaryLines: ["Waiting for tool result..."],
          rawResult: undefined,
        };

        pendingTools.push(toolItem);
        timelineItems.push({
          order: timelineItems.length,
          timestamp: toolItem.timestamp,
          item: toolItem,
        });
      }

      continue;
    }

    const matchedByNameIndex = pendingTools.findIndex((toolItem) => toolItem.toolName === entry.tool);
    const matchedPendingIndex = matchedByNameIndex >= 0 ? matchedByNameIndex : pendingTools.length > 0 ? 0 : -1;

    if (matchedPendingIndex >= 0) {
      const matchedTool = pendingTools[matchedPendingIndex];
      pendingTools.splice(matchedPendingIndex, 1);
      if (matchedTool) {
        matchedTool.timestamp = entry.timestamp;
        matchedTool.status = "success";
        matchedTool.summaryLines = summarizeResult(entry.result);
        matchedTool.rawResult = entry.result;
      }
      continue;
    }

    timelineItems.push({
      order: timelineItems.length,
      timestamp: entry.timestamp,
      item: {
        id: `trace-orphan-tool-${index + 1}-${entry.timestamp}`,
        kind: "tool",
        toolName: entry.tool,
        params: entry.params,
        timestamp: entry.timestamp,
        status: "success",
        summaryLines: summarizeResult(entry.result),
        rawResult: entry.result,
      },
    });
  }

  messages.forEach((message, index) => {
    if (isSyntheticRemediationMessage(message)) {
      return;
    }

    if (message.role === "assistant" && message.display?.thinking_raw) {
      timelineItems.push({
        order: timelineItems.length,
        timestamp: message.created_at,
        item: {
          id: `chat-thinking-${message.id}`,
          kind: "thinking",
          title: "Pre-answer reasoning",
          content: message.display.thinking_raw,
          timestamp: message.created_at,
          status: "completed",
        },
      });
    }

    if (message.role === "tool") {
      timelineItems.push({
        order: timelineItems.length,
        timestamp: message.created_at,
        item: {
          id: `chat-tool-${message.id}`,
          kind: "tool",
          toolName: message.tool_name ?? "tool_call",
          params: {},
          timestamp: message.created_at,
          status: "success",
          summaryLines: [message.content],
        },
      });
      return;
    }

    timelineItems.push({
      order: timelineItems.length,
      timestamp: message.created_at,
      item: {
        id: `chat-message-${message.id}-${index}`,
        kind: "message",
        role: message.role === "user" ? "user" : "assistant",
        content: message.display?.answer ?? message.content,
        timestamp: message.created_at,
        label: message.role === "user" ? "User input" : "Agent response",
      },
    });
  });

  buildSystemRecords(session, events, localAuditRecords).forEach((record, index) => {
    timelineItems.push({
      order: timelineItems.length + index,
      timestamp: record.timestamp,
      item: {
        id: record.id,
        kind: "system",
        eventKind: record.eventKind,
        summary: normalizeDiagnosisDisplayText(record.summary),
        details: record.details.map((detail) => normalizeDiagnosisDisplayText(detail)),
        timestamp: record.timestamp,
        statusTone: record.statusTone,
        source: record.source,
        dedupeKey: record.dedupeKey,
      },
    });
  });

  const sortedTimeline = [...timelineItems]
    .sort((left, right) => {
      const timeGap = new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
      if (timeGap !== 0) {
        return timeGap;
      }
      return left.order - right.order;
    })
    .map((entry) => entry.item);

  const latestTimelineTimestamp = sortedTimeline[sortedTimeline.length - 1]?.timestamp ?? getLatestTraceTimestamp(session);

  return {
    timeline: sortedTimeline,
    candidates: buildCandidates(session),
    hypotheses: buildHypotheses(session),
    propagationChain: buildPropagationChain(session),
    summary: resolveSummaryTimestamp(buildSummary(session), latestTimelineTimestamp),
    plan: buildPlan(session),
  };
}

function extractServiceName(prompt: string) {
  const matched = /([a-z0-9-]+(?:svc|service))/i.exec(prompt);
  return matched?.[1] ?? "auth-svc";
}

export function buildDiagnosisDemoScenario(prompt: string): DiagnosisDemoScenario {
  const now = Date.now();
  const serviceName = extractServiceName(prompt);
  const thinkingOneId = `demo-thinking-1-${now}`;
  const thinkingTwoId = `demo-thinking-2-${now}`;
  const thinkingThreeId = `demo-thinking-3-${now}`;
  const toolOneId = `demo-tool-metrics-${now}`;
  const toolTwoId = `demo-tool-logs-${now}`;
  const toolThreeId = `demo-tool-deploy-${now}`;

  const diagnosisResult: DiagnosisResult = {
    root_cause: "worker-03 \u8282\u70b9 GPU \u4e89\u7528",
    root_cause_layer: "platform",
    root_cause_entities: ["node:worker-03", "gpu:0"],
    confidence: 0.91,
    hypotheses: [
      {
        description: "GPU \u4e89\u7528",
        status: "confirmed",
        evidence_for: ["GPU util \u6301\u7eed 99%", "\u63a8\u7406\u8bf7\u6c42\u6392\u961f\u65f6\u957f\u540c\u6b65\u62ac\u5347"],
        evidence_against: [],
        confidence: 0.91,
      },
      {
        description: "RDMA \u6296\u52a8",
        status: "eliminated",
        evidence_for: [],
        evidence_against: ["\u76d1\u63a7\u4e2d RDMA \u91cd\u4f20\u672a\u62ac\u5347", "\u8de8\u8282\u70b9\u65f6\u5ef6\u672a\u51fa\u73b0\u540c\u6b65\u5f02\u5e38"],
        confidence: 0.32,
      },
      {
        description: "\u4e1a\u52a1\u7a81\u589e",
        status: "testing",
        evidence_for: ["\u8bf7\u6c42\u541e\u5410\u73af\u6bd4\u4e0b\u964d\u7ea6 18%"],
        evidence_against: [],
        confidence: 0.4,
      },
    ],
    propagation_chain: [
      {
        entity_id: "node:worker-03",
        entity_type: "node",
        metric: "gpu_util",
        value_before: "82%",
        value_after: "99%",
        description: "\u5ef6\u8fdf\u5347\u9ad8",
      },
    ],
    impact_summary: "vLLM p95 \u5ef6\u8fdf\u6301\u7eed\u8d70\u9ad8\uff0c\u63a8\u7406\u541e\u5410\u51fa\u73b0\u660e\u663e\u4e0b\u964d\u3002",
    affected_services: ["inference-gateway", "vllm-serving"],
    triage_priority: "P1",
    ranked_candidates: [
      {
        rank: 1,
        root_cause: "GPU \u4e89\u7528",
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03", "gpu:0"],
        confidence: 0.91,
        evidence_summary: "GPU util \u6301\u7eed 99%\uff0c\u8bf7\u6c42\u6392\u961f\u65f6\u957f\u4e0e\u8d85\u65f6\u544a\u8b66\u540c\u6b65\u51fa\u73b0\u3002",
        distinguishing_verification: "\u786e\u8ba4 worker-03 \u7684 GPU \u5f02\u5e38\u5360\u7528\u662f\u5426\u6765\u81ea\u989d\u5916\u8d1f\u8f7d\u6216\u8d44\u6e90\u51b2\u7a81\u3002",
      },
      {
        rank: 2,
        root_cause: "ECN \u914d\u7f6e\u4e0d\u4e00\u81f4",
        root_cause_layer: "network",
        root_cause_entities: ["switch:tor-07"],
        confidence: 0.62,
        evidence_summary: "ECN \u6ce2\u52a8\u5b58\u5728\u95f4\u6b47\u6027\u5f02\u5e38\uff0c\u4f46\u4e0d\u8db3\u4ee5\u89e3\u91ca\u5f53\u524d\u5168\u91cf\u75c7\u72b6\u3002",
        distinguishing_verification: "\u68c0\u67e5\u4ea4\u6362\u673a ECN \u914d\u7f6e\u5dee\u5f02\uff0c\u91cd\u70b9\u6bd4\u5bf9\u5f02\u5e38 TOR\u3002",
      },
      {
        rank: 3,
        root_cause: "\u6d41\u91cf\u7a81\u589e",
        root_cause_layer: "service",
        root_cause_entities: ["service:inference-gateway"],
        confidence: 0.4,
        evidence_summary: "\u8bf7\u6c42\u91cf\u867d\u6709\u4e0a\u5347\uff0c\u4f46\u4f4e\u4e8e\u5386\u53f2\u5cf0\u503c\uff0c\u7f3a\u5c11\u6392\u961f\u653e\u5927\u94fe\u8def\u8bc1\u636e\u3002",
        distinguishing_verification: "\u5bf9\u6bd4\u5386\u53f2\u5cf0\u503c\u65f6\u6bb5 QPS \u4e0e\u5ef6\u8fdf\u7684\u8026\u5408\u7a0b\u5ea6\u3002",
      },
    ],
    diagnosis_certainty: "confirmed",
    recommended_fix: {
      plan_id: `plan-gpu-contention-${now}`,
      root_cause: "worker-03 \u8282\u70b9 GPU \u4e89\u7528",
      description: "\u901a\u8fc7\u6700\u5c0f\u5f71\u54cd\u65b9\u5f0f\u7f13\u89e3\u5360\u7528\uff0c\u5e76\u9a8c\u8bc1\u5173\u952e\u6307\u6807\u6062\u590d\u540e\u518d\u9010\u6b65\u6062\u590d\u6d41\u91cf\u3002",
      steps: [
        {
          step_id: 1,
          description: "\u9694\u79bb worker-03 \u4e0a\u975e\u6838\u5fc3\u63a8\u7406\u8fdb\u7a0b\uff0c\u91ca\u653e GPU \u8d44\u6e90",
          tool: "node_quarantine",
          params: { node: "worker-03", scope: "non_core_inference" },
          rollback_tool: "node_unquarantine",
          verification: {
            method: "tool_call",
            tool: "check_gpu_utilization",
          },
          timeout: 120,
        },
        {
          step_id: 2,
          description: "\u5c06 10% \u6d41\u91cf\u5207\u81f3\u5065\u5eb7\u8282\u70b9\u5e76\u89c2\u6d4b 15 \u5206\u949f",
          tool: "traffic_shift",
          params: { from: "worker-03", to_pool: "healthy-gpu-pool", percentage: 10 },
          rollback_tool: "traffic_shift_rollback",
          verification: {
            method: "promql",
            query: "histogram_quantile(0.95, rate(vllm_request_latency_bucket[5m]))",
          },
          timeout: 300,
        },
        {
          step_id: 3,
          description: "\u786e\u8ba4\u5ef6\u8fdf\u548c\u9519\u8bef\u7387\u6062\u590d\u540e\u9010\u6b65\u6062\u590d\u4e1a\u52a1\u6d41\u91cf",
          tool: "traffic_restore",
          params: { target: "worker-03", strategy: "gradual" },
          verification: {
            method: "wait",
            wait_seconds: 600,
          },
          timeout: 900,
        },
      ],
      canary: {
        enabled: true,
        target_percentage: 10,
        monitor_duration: 15,
        success_criteria: [
          { metric: "vllm_p95_ms", operator: "<=", value: 1800 },
          { metric: "inference_error_rate", operator: "<=", value: "1%" },
        ],
      },
      estimated_impact: "\u6d41\u91cf\u5207\u6362\u9636\u6bb5\u53ef\u80fd\u4ea7\u751f\u77ed\u65f6\u6296\u52a8\uff0c\u4f46\u6574\u4f53\u98ce\u9669\u53ef\u63a7\u3002",
      confidence: 0.91,
      priority: "P1",
      safety_level: "\u9700\u8981\u4eba\u5de5\u786e\u8ba4",
    },
  };

  const demoSession: DiagnosisSession = {
    session_id: `demo-session-${now}`,
    alert: {
      alert_name: "vLLM \u63a8\u7406\u5ef6\u8fdf\u5347\u9ad8",
      severity: "critical",
      labels: { service: serviceName, component: "inference" },
      annotations: { summary: "vLLM p95 latency \u6301\u7eed\u5347\u9ad8" },
      starts_at: new Date(now - 5 * 60_000).toISOString(),
      fingerprint: `demo-fp-${now}`,
      status: "firing",
      source: "alertmanager",
    },
    status: "approval_required",
    duration_seconds: 0,
    diagnosis_result: diagnosisResult,
  };

  const summary =
    buildSummary(demoSession) ??
    ({
      title: "\u6839\u56e0\u8bca\u65ad",
      subtitle: "\u57fa\u4e8e\u73b0\u6709\u8bc1\u636e\u6574\u7406\u51fa\u7684\u5f53\u524d\u7ed3\u8bba\u3002",
      certaintyLabel: "\u5206\u6790\u4e2d",
      certaintyTone: "info",
      confidenceLabel: "--",
      confidenceRawLabel: "--",
      updatedTimeLabel: "--",
      updatedDateTimeLabel: "--",
      affectedServices: [],
      impactSummary: "\u7b49\u5f85\u63a8\u7406\u601d\u8003\u8f68\u8ff9\u4e0e\u5de5\u5177\u89c2\u5bdf\u7ed3\u679c\u3002",
    } satisfies DiagnosisSummaryView);

  const candidates = buildCandidates(demoSession);
  const hypotheses = buildHypotheses(demoSession);
  const propagationChain = buildPropagationChain(demoSession);

  const plan =
    buildPlan(demoSession) ??
    ({
      title: "\u8bca\u65ad\u4fee\u590d\u5efa\u8bae",
      description: "\u5f85\u751f\u6210\u540e\u5c55\u793a\u53ef\u6267\u884c\u65b9\u6848\u3002",
      priorityLabel: diagnosisResult.triage_priority,
      confidenceLabel: getConfidenceLabel(diagnosisResult.confidence),
      steps: [],
    } satisfies DiagnosisPlanView);

  const initialTimeline: DiagnosisTimelineItem[] = [
      {
        id: `demo-user-${now}`,
        kind: "message",
        role: "user",
        content: prompt,
        timestamp: new Date(now).toISOString(),
        label: "\u7528\u6237\u8f93\u5165",
      },
    ];

  const events: DiagnosisDemoEvent[] = [
      {
        delayMs: 240,
        type: "append",
        item: {
          id: thinkingOneId,
          kind: "thinking",
          title: "\u7406\u89e3\u7528\u6237\u8bf7\u6c42",
          content: `\u5148\u786e\u8ba4 ${serviceName} \u7684\u5173\u952e\u6307\u6807\uff0c\u518d\u7ed3\u5408\u8282\u70b9\u8d44\u6e90\u3001\u9519\u8bef\u65e5\u5fd7\u548c\u90e8\u7f72\u8bb0\u5f55\u7f29\u5c0f\u6392\u67e5\u8303\u56f4\u3002`,
          timestamp: new Date(now + 240).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 480,
        type: "append",
        item: {
          id: `demo-assistant-next-step-1-${now}`,
          kind: "message",
          role: "assistant",
          content: `\u7b2c\u4e00\u6b65\u4f1a\u5148\u62c9\u53d6 ${serviceName} \u7684\u5173\u952e\u6307\u6807\uff0c\u786e\u8ba4\u5ef6\u8fdf\u4e0e\u8d44\u6e90\u4f7f\u7528\u662f\u5426\u540c\u6b65\u5f02\u5e38\uff0c\u518d\u5224\u65ad\u662f\u4e0d\u662f\u8282\u70b9\u7ea7\u95ee\u9898\u3002`,
          timestamp: new Date(now + 480).toISOString(),
          label: "\u4e0b\u4e00\u6b65\u884c\u52a8",
        },
      },
      {
        delayMs: 560,
        type: "append",
        item: {
          id: toolOneId,
          kind: "tool",
          toolName: "query_service_metrics",
          params: {
            service: serviceName,
            window: "-30m",
            focus: ["vllm_p95", "gpu_util", "throughput"],
          },
          timestamp: new Date(now + 560).toISOString(),
          status: "loading",
          summaryLines: ["\u62c9\u53d6\u6700\u8fd1 30 \u5206\u949f\u5173\u952e\u6307\u6807..."],
        },
      },
      {
        delayMs: 920,
        type: "update_tool",
        targetId: toolOneId,
        summaryLines: ["vllm_p95: 1.6s -> 3.4s", "gpu_util: 82% -> 99%", "throughput: -18%"],
        rawResult: {
          vllm_p95: "3.4s",
          gpu_util: "99%",
          throughput_drop: "18%",
        },
      },
      {
        delayMs: 1120,
        type: "update_thinking",
        targetId: thinkingOneId,
        status: "completed",
      },
      {
        delayMs: 1260,
        type: "append",
        item: {
          id: thinkingTwoId,
          kind: "thinking",
          title: "\u7f29\u5c0f\u5019\u9009\u8def\u5f84",
          content: "GPU \u4e89\u7528\u4e0e\u5ef6\u8fdf\u540c\u6b65\u62ac\u5347\uff0c\u9700\u9a8c\u8bc1\u662f\u5426\u5b58\u5728\u8282\u70b9\u7ea7\u5f02\u5e38\uff0c\u540c\u65f6\u6392\u9664\u7f51\u7edc\u5c42\u548c\u6d41\u91cf\u7a81\u589e\u3002",
          timestamp: new Date(now + 1260).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 1400,
        type: "append",
        item: {
          id: `demo-assistant-next-step-2-${now}`,
          kind: "message",
          role: "assistant",
          content: "\u4e0b\u4e00\u6b65\u4f1a\u6293\u53d6\u9519\u8bef\u65e5\u5fd7\uff0c\u9a8c\u8bc1\u662f\u5426\u5b58\u5728 worker-03 \u7684\u8d85\u65f6\u5f02\u5e38\u4e0e\u989d\u5916\u5360\u7528\uff0c\u518d\u51b3\u5b9a\u662f\u5426\u6267\u884c\u6d41\u91cf\u8fc1\u79fb\u3002",
          timestamp: new Date(now + 1400).toISOString(),
          label: "\u4e0b\u4e00\u6b65\u884c\u52a8",
        },
      },
      {
        delayMs: 1500,
        type: "append",
        item: {
          id: toolTwoId,
          kind: "tool",
          toolName: "fetch_error_logs",
          params: {
            service: serviceName,
            level: "error",
            limit: 120,
          },
          timestamp: new Date(now + 1500).toISOString(),
          status: "loading",
          summaryLines: ["\u62c9\u53d6\u9519\u8bef\u65e5\u5fd7\u5e76\u5339\u914d\u5f02\u5e38\u8282\u70b9..."],
        },
      },
      {
        delayMs: 1860,
        type: "update_tool",
        targetId: toolTwoId,
        summaryLines: [
          "node=worker-03 \u51fa\u73b0 GPU queue timeout",
          "\u5f02\u5e38\u8fdb\u7a0b python llm_worker.py \u5360\u7528\u8fbe\u5230 97%",
          "RDMA retransmit \u672a\u51fa\u73b0\u660e\u663e\u5f02\u5e38",
        ],
        rawResult: {
          hotspot_node: "worker-03",
          gpu_queue_timeout: true,
          rdma_retransmit_anomaly: false,
        },
      },
      {
        delayMs: 2060,
        type: "update_thinking",
        targetId: thinkingTwoId,
        status: "completed",
      },
      {
        delayMs: 2460,
        type: "append",
        item: {
          id: toolThreeId,
          kind: "tool",
          toolName: "get_recent_deployments",
          params: {
            service: serviceName,
            limit: 2,
          },
          timestamp: new Date(now + 2460).toISOString(),
          status: "loading",
          summaryLines: ["\u62c9\u53d6\u6700\u8fd1\u90e8\u7f72\u8bb0\u5f55..."],
        },
      },
      {
        delayMs: 2820,
        type: "update_tool",
        targetId: toolThreeId,
        summaryLines: ["deploy_version: v2.8.4", "deployed_at: 12 \u5206\u949f\u524d", "change_note: \u8c03\u6574\u63a8\u7406\u5e76\u53d1\u914d\u7f6e"],
        rawResult: {
          deploy_version: "v2.8.4",
          deployed_at: "12m ago",
          change_note: "adjust inference concurrency",
        },
      },
      {
        delayMs: 3220,
        type: "append",
        item: {
          id: thinkingThreeId,
          kind: "thinking",
          title: "\u5f62\u6210\u6700\u7ec8\u7ed3\u8bba",
          content: "\u8bc1\u636e\u6700\u7ec8\u6536\u655b\u5230 worker-03 GPU \u4e89\u7528\u4e0e\u5f02\u5e38\u5360\u7528\uff0c\u6392\u961f\u7b49\u5f85\u4e0e\u5ef6\u8fdf\u540c\u6b65\u62ac\u5347\uff0c\u540c\u65f6\u5df2\u53cd\u8bc1\u6392\u9664 RDMA \u6296\u52a8\u3002",
          timestamp: new Date(now + 3220).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 3460,
        type: "update_thinking",
        targetId: thinkingThreeId,
        status: "completed",
      },
      {
        delayMs: 3560,
        type: "append",
        item: {
          id: `demo-assistant-final-${now}`,
          kind: "message",
          role: "assistant",
          content: "\u5f53\u524d\u7ed3\u8bba\u4e3a worker-03 \u8282\u70b9 GPU \u4e89\u7528\u3002\u4e0b\u65b9\u5361\u7247\u5df2\u540c\u6b65\u5019\u9009\u6839\u56e0\u3001\u5173\u952e\u8bc1\u636e\u3001\u533a\u5206\u9a8c\u8bc1\u548c\u4fee\u590d\u5efa\u8bae\u3002",
          timestamp: new Date(now + 3560).toISOString(),
          label: "\u6700\u7ec8\u7ed3\u8bba",
        },
      },
      {
        delayMs: 3860,
        type: "complete",
      },
    ];

  const appendEventTimestamps = events
    .filter((event): event is Extract<DiagnosisDemoEvent, { type: "append" }> => event.type === "append")
    .map((event) => event.item.timestamp);
  const latestDemoTimestamp = [...initialTimeline.map((item) => item.timestamp), ...appendEventTimestamps].at(-1);

  return {
    initialTimeline,
    events,
    candidates,
    hypotheses,
    propagationChain,
    summary: resolveSummaryTimestamp(summary, latestDemoTimestamp) ?? summary,
    plan,
  };
}
