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
  eventKind:
    | "approval_result"
    | "canary_progress"
    | "execution_progress"
    | "metric_feedback"
    | "alert_recovery"
    | "session_closed";
  summary: string;
  details: string[];
  timestamp: string;
  statusTone: ChipTone;
  progress?: {
    label: string;
    value: number;
    helper?: string;
  };
  source: "optimistic" | "event" | "local_audit";
  dedupeKey: string;
  stage?: string;
  runId?: string;
  isExecutionRunEvent?: boolean;
  metricLines?: string[];
};

export type DiagnosisReportTimelineItem = {
  id: string;
  kind: "report";
  timestamp: string;
  summary: DiagnosisSummaryView;
  candidates: DiagnosisCandidateView[];
  hypotheses?: DiagnosisHypothesisView[];
  propagationChain?: DiagnosisPropagationStepView[];
  planStatusLabel?: string;
  planStatusTone?: ChipTone;
};

export type DiagnosisRunStatus = "running" | "success" | "error" | "timeout";

export type DiagnosisRunToolView = {
  id: string;
  toolName: string;
  params: Record<string, unknown>;
  timestamp: string;
  status: TimelineToolStatus;
  summaryLines: string[];
  rawResult?: Record<string, unknown> | null;
  stepId?: string;
};

export type DiagnosisRunStepView = {
  id: string;
  eventKind: DiagnosisSystemEventView["eventKind"];
  stage?: string;
  title: string;
  summary: string;
  details: string[];
  timestamp: string;
  status: DiagnosisRunStatus;
  statusTone: ChipTone;
  progress?: DiagnosisSystemEventView["progress"];
  metricLines: string[];
  toolIds: string[];
};

export type DiagnosisRunPhase = "single" | "canary" | "full";

export type DiagnosisRunTimelineItem = {
  id: string;
  kind: "run";
  runId: string;
  phase: DiagnosisRunPhase;
  title: string;
  timestamp: string;
  status: DiagnosisRunStatus;
  progress: {
    label: string;
    value: number;
    helper?: string;
  };
  currentStageLabel: string;
  startedAt: string;
  updatedAt: string;
  steps: DiagnosisRunStepView[];
  tools: DiagnosisRunToolView[];
  metrics: string[];
};

type DiagnosisSystemRecord = DiagnosisLocalAuditRecord & {
  stage?: string;
  runId?: string;
  isExecutionRunEvent?: boolean;
  metricLines?: string[];
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
    }
  | DiagnosisReportTimelineItem
  | DiagnosisRunTimelineItem;

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

type DiagnosisPlanStatusView = {
  label: string;
  tone: ChipTone;
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

const ANSI_ESCAPE_PATTERN = /\u001b\[[0-?]*[ -/]*[@-~]/g;
const UNICODE_FORMAT_CHARS_PATTERN = /[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF]/g;

function stripControlCharacters(text: string) {
  return text
    .replace(ANSI_ESCAPE_PATTERN, "")
    .replace(UNICODE_FORMAT_CHARS_PATTERN, "")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "");
}

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
  let current = stripControlCharacters(text);

  for (let index = 0; index < 3; index += 1) {
    const decodedUnicode = decodeUnicodeEscapes(current);
    const repairedMojibake = repairUtf8Mojibake(decodedUnicode);
    if (repairedMojibake === current) {
      break;
    }
    current = repairedMojibake;
  }

  return stripControlCharacters(current);
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
  const updatedLabels = getUpdatedLabelParts(getLatestTraceTimestamp(session));
  if (!result) {
    return session
      ? {
          title: "\u6839\u56e0\u8bca\u65ad",
          subtitle: "\u5f53\u524d\u4f1a\u8bdd\u5c1a\u672a\u5f62\u6210\u6700\u7ec8\u8bca\u65ad\u7ed3\u8bba\u3002",
          certaintyLabel: "\u5206\u6790\u4e2d",
          certaintyTone: "info",
          confidenceLabel: "--",
          confidenceRawLabel: "--",
          priorityLabel: undefined,
          sessionLabel: session.session_id,
          updatedTimeLabel: updatedLabels.updatedTimeLabel,
          updatedDateTimeLabel: updatedLabels.updatedDateTimeLabel,
          affectedServices: [],
          impactSummary: "\u7b49\u5f85\u63a8\u7406\u601d\u8003\u8f68\u8ff9\u4e0e\u5de5\u5177\u89c2\u5bdf\u7ed3\u679c\u3002",
          rootCause: undefined,
          rootCauseLayer: undefined,
          rootCauseLayerLabel: "\u5c42\u7ea7",
          rootCauseEntities: [],
        }
      : undefined;
  }

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

function buildPlanStatus(session: DiagnosisSession | undefined, plan: DiagnosisPlanView | undefined): DiagnosisPlanStatusView | undefined {
  if (!plan) {
    return undefined;
  }

  const normalizedStatus = String(session?.status ?? "").trim().toLowerCase();
  switch (normalizedStatus) {
    case "approval_required":
      return {
        label: "\u4fee\u590d\u65b9\u6848\u5df2\u751f\u6210\uff0c\u7b49\u5f85\u5ba1\u6279",
        tone: "warning",
      };
    case "approved":
    case "remediating":
    case "validating":
      return {
        label: "\u4fee\u590d\u6d41\u7a0b\u5df2\u542f\u52a8\uff0c\u6b63\u5728\u6267\u884c\u4e0e\u9a8c\u8bc1",
        tone: "accent",
      };
    case "rejected":
      return {
        label: "\u4fee\u590d\u65b9\u6848\u672a\u901a\u8fc7\u5ba1\u6279",
        tone: "danger",
      };
    case "resolved":
    case "closed":
      return {
        label: "\u4fee\u590d\u6d41\u7a0b\u5df2\u5b8c\u6210",
        tone: "success",
      };
    default:
      return {
        label: "\u4fee\u590d\u65b9\u6848\u5df2\u5907\u59a5",
        tone: "info",
      };
  }
}

function buildReportTimelineItem(
  session: DiagnosisSession | undefined,
  timestamp: string | undefined,
  summary: DiagnosisSummaryView | undefined,
  candidates: DiagnosisCandidateView[],
  hypotheses: DiagnosisHypothesisView[],
  propagationChain: DiagnosisPropagationStepView[],
  plan: DiagnosisPlanView | undefined,
): DiagnosisReportTimelineItem | undefined {
  if (!session?.diagnosis_result || !summary || !timestamp) {
    return undefined;
  }

  const planStatus = buildPlanStatus(session, plan);
  return {
    id: `diagnosis-report-${session.session_id}`,
    kind: "report",
    timestamp,
    summary,
    candidates,
    hypotheses,
    propagationChain,
    planStatusLabel: planStatus?.label,
    planStatusTone: planStatus?.tone,
  };
}

function getReportTimelineTimestamp(
  session: DiagnosisSession | undefined,
  timeline: DiagnosisTimelineItem[],
) {
  const latestTraceTimestamp = getLatestTraceTimestamp(session);
  if (latestTraceTimestamp) {
    return latestTraceTimestamp;
  }

  const latestAssistantTimestamp = [...timeline]
    .reverse()
    .find(
      (
        item,
      ): item is Extract<DiagnosisTimelineItem, { kind: "message" }> =>
        item.kind === "message" && item.role === "assistant",
    )?.timestamp;
  if (latestAssistantTimestamp) {
    return latestAssistantTimestamp;
  }

  const latestNarrativeTimestamp = [...timeline]
    .reverse()
    .find((item) => item.kind !== "system" && item.kind !== "report")?.timestamp;
  return latestNarrativeTimestamp ?? session?.alert.starts_at;
}

function buildTraceNextAction(entry: ThinkingStep) {
  if (entry.action_type === "tool_call" && entry.tool_name) {
    const serviceHint =
      typeof entry.tool_params?.service === "string" && entry.tool_params.service.trim().length > 0
        ? ` for ${entry.tool_params.service}`
        : "";
    return `Next action: call ${entry.tool_name}${serviceHint} to validate this hypothesis.`;
  }

  if (entry.action_type === "conclude") {
    return "Next action: synthesize the current evidence and provide the root-cause conclusion.";
  }

  return "Next action: continue gathering discriminative evidence to narrow the root cause.";
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
    return ["\u6267\u884c\u6b65\u9aa4\uff1a--"];
  }

  return steps.map((step, index) => "\u6b65\u9aa4 " + (index + 1) + "\uff1a" + normalizeDiagnosisDisplayText(step.description));
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
  const versionLabel = planVersion ? "v" + planVersion : "v?";
  const approver = resolveRecordUser(data);
  const details = [
    "\u5ba1\u6279\u65f6\u95f4\uff1a" + formatDateTime(event.timestamp),
    "\u65b9\u6848\u7248\u672c\uff1a" + (planVersion ? "v" + planVersion : "--"),
    "\u65b9\u6848 ID\uff1a" + (plan?.plan_id ?? "--"),
    "\u5ba1\u6279\u4eba\uff1a" + approver,
    "\u5ba1\u6279\u52a8\u4f5c\uff1a\u540c\u610f\uff0c\u8fdb\u5165\u6267\u884c",
    "\u65b9\u6848\u6807\u9898\uff1a" + (plan ? normalizeDiagnosisDisplayText(plan.root_cause) : "--"),
    ...buildPlanStepDetailLines(session?.diagnosis_result),
  ];

  return {
    id: "event-approval-approved-" + event.timestamp,
    sessionId: event.session_id,
    eventKind: "approval_result",
    source: "event",
    dedupeKey: "approval-result-approved-" + versionLabel,
    timestamp: event.timestamp,
    summary: "[\u7cfb\u7edf] \u5df2\u5b8c\u6210\u6267\u884c\u786e\u8ba4\uff08" + versionLabel + "\uff0c\u5ba1\u6279\u4eba " + approver + "\uff09",
    details,
    statusTone: "success",
  };
}

function hasCanaryPlan(session: DiagnosisSession | undefined) {
  return Boolean(session?.diagnosis_result?.recommended_fix?.canary?.enabled);
}

function clampProgress(value: unknown): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return Math.max(0, Math.min(100, Math.round(parsed)));
}

function getExecutionStageLabel(stage: string, session?: DiagnosisSession) {
  const canaryEnabled = hasCanaryPlan(session);
  switch (stage) {
    case "approval_confirmed":
      return "\u5ba1\u6279\u786e\u8ba4";
    case "execution_started":
      return canaryEnabled ? "\u5f00\u59cb\u6267\u884c" : "\u5f00\u59cb\u6267\u884c";
    case "canary_started":
      return "\u5f00\u59cb\u7070\u5ea6\u6267\u884c";
    case "canary_progress":
    case "canary_batch_progress":
      return "\u7070\u5ea6\u6267\u884c\u4e2d";
    case "canary_succeeded":
    case "canary_completed":
      return "\u7070\u5ea6\u6267\u884c\u6210\u529f";
    case "observation_started":
      return "\u7070\u5ea6\u89c2\u5bdf\u4e2d";
    case "observation_result":
      return "\u7070\u5ea6\u89c2\u5bdf\u7ed3\u679c";
    case "full_rollout_started":
      return "\u5f00\u59cb\u5168\u91cf\u4fee\u590d";
    case "full_rollout_progress":
      return "\u5168\u91cf\u4fee\u590d\u4e2d";
    case "full_rollout_succeeded":
      return "\u5168\u91cf\u4fee\u590d\u6210\u529f";
    case "alert_recovered":
      return "\u544a\u8b66\u5df2\u6062\u590d";
    case "session_closed":
      return "\u4f1a\u8bdd\u5df2\u5173\u95ed";
    case "execution_mocked":
      return "\u6a21\u62df\u6267\u884c\u5b8c\u6210";
    case "execution_succeeded":
      return canaryEnabled ? "\u5168\u91cf\u4fee\u590d\u6210\u529f" : "\u6267\u884c\u6210\u529f";
    case "execution_failed":
      return "\u6267\u884c\u5931\u8d25";
    case "execution_timeout":
      return "\u6267\u884c\u8d85\u65f6";
    case "escalation_required":
      return "\u9700\u8981\u5347\u7ea7\u5904\u7406";
    default:
      return stage || "\u6267\u884c\u72b6\u6001\u66f4\u65b0";
  }
}

function getExecutionStageTone(stage: string): ChipTone {
  switch (stage) {
    case "approval_confirmed":
    case "canary_succeeded":
    case "canary_completed":
    case "observation_result":
    case "full_rollout_succeeded":
    case "alert_recovered":
    case "session_closed":
    case "execution_mocked":
    case "execution_succeeded":
      return "success";
    case "execution_failed":
    case "execution_timeout":
    case "escalation_required":
      return "danger";
    case "execution_started":
    case "canary_started":
    case "canary_progress":
    case "canary_batch_progress":
    case "observation_started":
    case "full_rollout_started":
    case "full_rollout_progress":
      return "warning";
    default:
      return "info";
  }
}

function getExecutionEventKind(stage: string): DiagnosisSystemEventView["eventKind"] {
  if (["canary_started", "canary_progress", "canary_batch_progress", "canary_succeeded", "canary_completed"].includes(stage)) {
    return "canary_progress";
  }
  if (["observation_started", "observation_result"].includes(stage)) {
    return "metric_feedback";
  }
  if (stage === "alert_recovered") {
    return "alert_recovery";
  }
  if (stage === "session_closed") {
    return "session_closed";
  }
  return "execution_progress";
}

function getDefaultExecutionMessage(
  stage: string,
  data: Record<string, unknown>,
  session: DiagnosisSession | undefined,
) {
  const rawMessage = typeof data.message === "string" && data.message.trim()
    ? normalizeDiagnosisDisplayText(data.message)
    : "";
  if (rawMessage) {
    return rawMessage;
  }

  const canaryEnabled = hasCanaryPlan(session);
  if (stage === "approval_confirmed") {
    return "\u5ba1\u6279\u901a\u8fc7\uff0c\u51c6\u5907\u5f00\u59cb\u6267\u884c";
  }
  if (stage === "execution_started" && canaryEnabled) {
    return "\u5ba1\u6279\u901a\u8fc7\uff0c\u5148\u8fdb\u5165\u7070\u5ea6\u6267\u884c\u5e76\u6301\u7eed\u89c2\u6d4b";
  }
  if (stage === "canary_started") {
    return "\u5f00\u59cb\u7070\u5ea6\u6267\u884c\uff0c\u8c03\u7528\u8bca\u65ad skill \u8fdb\u884c\u5c0f\u6d41\u91cf\u9a8c\u8bc1";
  }
  if (stage === "canary_succeeded" || stage === "canary_completed") {
    return "\u7070\u5ea6\u6279\u6b21\u9a8c\u8bc1\u901a\u8fc7\uff0c\u7ee7\u7eed\u89c2\u5bdf\u6307\u6807\u540e\u518d\u63a8\u8fdb\u5168\u91cf";
  }
  if (stage === "observation_started") {
    return "\u5f00\u59cb\u7070\u5ea6\u89c2\u5bdf\uff0c\u6301\u7eed\u91c7\u96c6\u5ef6\u8fdf\u548c\u9519\u8bef\u7387\u6307\u6807";
  }
  if (stage === "observation_result") {
    const ok = data.metrics_improved === true && data.alert_cleared !== false;
    return ok
      ? "\u89c2\u5bdf\u7ed3\u8bba\u826f\u597d\uff0c\u51c6\u5907\u63a8\u8fdb\u5168\u91cf\u4fee\u590d"
      : "\u89c2\u5bdf\u7ed3\u8bba\u672a\u901a\u8fc7\uff0c\u5efa\u8bae\u6682\u505c\u5168\u91cf\u5e76\u7ee7\u7eed\u6392\u67e5";
  }
  if (stage === "full_rollout_started") {
    return "\u5f00\u59cb\u5168\u91cf\u4fee\u590d";
  }
  if (stage === "full_rollout_succeeded") {
    return "\u5168\u91cf\u4fee\u590d\u5b8c\u6210\uff0c\u6301\u7eed\u89c2\u5bdf\u786e\u8ba4\u4e1a\u52a1\u6062\u590d";
  }
  if (stage === "alert_recovered") {
    return "\u76f8\u5173\u62a5\u8b66\u5df2\u7ecf\u6062\u590d";
  }
  if (stage === "session_closed") {
    return "\u6267\u884c\u4f1a\u8bdd\u5df2\u7ed3\u675f";
  }
  if (stage === "execution_succeeded" && canaryEnabled) {
    return "\u5168\u91cf\u4fee\u590d\u6210\u529f\uff0c\u6d41\u7a0b\u6267\u884c\u5b8c\u6210";
  }
  return "";
}

function getExecutionProgress(
  stage: string,
  data: Record<string, unknown>,
  session: DiagnosisSession | undefined,
): DiagnosisSystemEventView["progress"] | undefined {
  const explicitProgress = clampProgress(data.progress ?? data.progress_percent ?? data.percentage);
  const canaryEnabled = hasCanaryPlan(session);
  let value = explicitProgress;

  if (value === null) {
    if (stage === "execution_started" && canaryEnabled) value = 5;
    else if (stage === "canary_started") value = 10;
    else if (stage === "canary_progress" || stage === "canary_batch_progress") value = 50;
    else if (stage === "canary_succeeded" || stage === "canary_completed") value = 100;
    else if (stage === "full_rollout_started") value = 20;
    else if (stage === "full_rollout_progress") value = 65;
    else if (stage === "full_rollout_succeeded" || stage === "execution_succeeded") value = 100;
    else if (stage === "alert_recovered" || stage === "session_closed") value = 100;
  }

  if (value === null) {
    return undefined;
  }

  const label =
    stage.includes("canary") || (stage === "execution_started" && canaryEnabled)
      ? "\u7070\u5ea6\u8fdb\u5ea6"
      : stage.includes("full_rollout")
        ? "\u5168\u91cf\u8fdb\u5ea6"
        : "\u6267\u884c\u8fdb\u5ea6";
  const helper = typeof data.progress_label === "string" && data.progress_label.trim()
    ? normalizeDiagnosisDisplayText(data.progress_label)
    : typeof data.batch === "string" && data.batch.trim()
      ? normalizeDiagnosisDisplayText(data.batch)
      : undefined;

  return { label, value, helper };
}

function resolveExecutionRunId(data: Record<string, unknown>, sessionId: string) {
  const idKeys = [
    "rollout_id",
    "rolloutId",
    "trace_id",
    "traceId",
    "task_id",
    "taskId",
    "execution_id",
    "executionId",
  ];
  for (const key of idKeys) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return `${sessionId}-execution-run`;
}

function getStreamFirstTimelinePriority(item: DiagnosisTimelineItem) {
  if (item.kind === "thinking") {
    return 0;
  }
  if (item.kind === "tool") {
    return 1;
  }
  if (item.kind === "message") {
    return 2;
  }
  if (item.kind === "run") {
    return 3;
  }
  if (item.kind === "system") {
    return 4;
  }
  if (item.kind === "report") {
    return 5;
  }
  return 99;
}

function keepLatestSingleReportItem(timeline: DiagnosisTimelineItem[]) {
  let latestReportIndex = -1;
  for (let index = 0; index < timeline.length; index += 1) {
    if (timeline[index]?.kind === "report") {
      latestReportIndex = index;
    }
  }

  if (latestReportIndex < 0) {
    return timeline;
  }

  return timeline.filter(
    (item, index) => item.kind !== "report" || index === latestReportIndex,
  );
}

function extractExecutionMetricLines(details: string[]) {
  const metricPatterns = [
    "\u6307\u6807",
    "\u62a5\u8b66",
    "p95",
    "p99",
    "\u9519\u8bef\u7387",
    "error",
    "GPU",
    "util",
    "\u961f\u5217",
    "\u5ef6\u8fdf",
    "latency",
  ];
  return details.filter((detail) => {
    const normalized = detail.toLowerCase();
    return metricPatterns.some((pattern) => normalized.includes(pattern.toLowerCase()));
  });
}

function buildExecutionProgressRecord(
  event: SessionEvent,
  session: DiagnosisSession | undefined,
): DiagnosisSystemRecord | null {
  if (event.type !== "remediation_progress" && event.type !== "observation_result") {
    return null;
  }

  const data = isRecord(event.data) ? event.data : {};
  const stage = String(data.stage ?? event.type ?? "").trim().toLowerCase();
  const supportedStages = [
    "approval_confirmed",
    "execution_started",
    "canary_started",
    "canary_progress",
    "canary_batch_progress",
    "canary_succeeded",
    "canary_completed",
    "observation_started",
    "observation_result",
    "full_rollout_started",
    "full_rollout_progress",
    "full_rollout_succeeded",
    "execution_mocked",
    "execution_succeeded",
    "execution_failed",
    "execution_timeout",
    "escalation_required",
    "alert_recovered",
    "session_closed",
  ];
  if (!supportedStages.includes(stage)) {
    return null;
  }

  const stageLabel = getExecutionStageLabel(stage, session);
  const message = getDefaultExecutionMessage(stage, data, session);
  const operator = resolveRecordUser(data);
  const runId = resolveExecutionRunId(data, event.session_id);
  const progress = getExecutionProgress(stage, data, session);
  const details = [
    "\u6267\u884c\u65f6\u95f4\uff1a" + formatDateTime(event.timestamp),
    "\u6267\u884c\u9636\u6bb5\uff1a" + stageLabel,
    "\u6267\u884c\u4eba\uff1a" + operator,
  ];

  if (message) {
    details.push("\u6267\u884c\u8bf4\u660e\uff1a" + message);
  }

  const skillId = typeof data.skill_id === "string" && data.skill_id.trim()
    ? data.skill_id.trim()
    : typeof data.skill === "string" && data.skill.trim()
      ? data.skill.trim()
      : "";
  if (skillId) {
    details.push("\u8c03\u7528 skill\uff1a" + skillId);
  }

  if (progress) {
    details.push(
      progress.label
      + "\uff1a"
      + progress.value
      + "%"
      + (progress.helper ? "\uff08" + progress.helper + "\uff09" : ""),
    );
  }

  if (typeof data.metrics_improved === "boolean") {
    details.push("\u6307\u6807\u53cd\u9988\uff1a" + (data.metrics_improved ? "\u5df2\u6062\u590d" : "\u672a\u6062\u590d"));
  }
  if (typeof data.alert_cleared === "boolean") {
    details.push("\u544a\u8b66\u72b6\u6001\uff1a" + (data.alert_cleared ? "\u5df2\u6062\u590d" : "\u672a\u6062\u590d"));
  }

  const timeoutSeconds = Number(data.timeout_seconds ?? 0);
  if (Number.isFinite(timeoutSeconds) && timeoutSeconds > 0) {
    details.push("\u8d85\u65f6\u4e0a\u9650\uff1a" + timeoutSeconds + "s");
  }

  const stepResults = Array.isArray(data.step_results) ? data.step_results : [];
  stepResults.slice(0, 3).forEach((step, index) => {
    if (!isRecord(step)) {
      return;
    }
    const tool = typeof step.tool === "string" ? step.tool : "step";
    const command = typeof step.command === "string" ? step.command : "";
    details.push("\u7ec6\u8282 " + (index + 1) + "\uff1a" + tool + (command ? " | " + normalizeDiagnosisDisplayText(command) : ""));
  });

  const metricLines = extractExecutionMetricLines(details);

  return {
    id: "event-" + stage + "-" + event.timestamp,
    sessionId: event.session_id,
    eventKind: getExecutionEventKind(stage),
    source: "event",
    dedupeKey: "execution-progress-" + stage + "-" + event.timestamp,
    timestamp: event.timestamp,
    summary: message ? "[\u7cfb\u7edf] " + stageLabel + "\uff1a" + message : "[\u7cfb\u7edf] " + stageLabel,
    details,
    statusTone: getExecutionStageTone(stage),
    stage,
    runId,
    isExecutionRunEvent: true,
    metricLines,
    progress,
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
      buildExecutionProgressRecord(event, session),
    ].filter((item): item is DiagnosisSystemRecord => Boolean(item));
    return items;
  });

  const deduped = new Map<string, DiagnosisSystemRecord>();
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

function isExecutionRunSystemItem(
  item: DiagnosisTimelineItem,
): item is DiagnosisSystemEventView {
  return (
    item.kind === "system" &&
    item.eventKind !== "approval_result" &&
    item.eventKind !== "metric_feedback" &&
    item.eventKind !== "alert_recovery" &&
    item.eventKind !== "session_closed" &&
    (item.isExecutionRunEvent === true ||
      [
        "canary_progress",
        "execution_progress",
      ].includes(item.eventKind))
  );
}
function getRunStepStatus(item: DiagnosisSystemEventView): DiagnosisRunStatus {
  const normalizedSummary = item.summary.toLowerCase();
  if (item.statusTone === "danger") {
    return normalizedSummary.includes("timeout") || item.summary.includes("\u8d85\u65f6")
      ? "timeout"
      : "error";
  }
  if (item.statusTone === "success") {
    return "success";
  }
  return "running";
}

function getRunStatusFromSteps(steps: DiagnosisRunStepView[]): DiagnosisRunStatus {
  const latest = steps[steps.length - 1];
  if (!latest) {
    return "running";
  }
  return latest.status;
}

function getRunStepTitle(item: DiagnosisSystemEventView) {
  if (item.stage) {
    return getExecutionStageLabel(item.stage);
  }
  const normalizedSummary = item.summary.replace(/^\[\u7cfb\u7edf\]\s*/, "");
  const [title] = normalizedSummary.split(/[\uff1a:]/);
  return title?.trim() || "\u6267\u884c\u8fdb\u5ea6";
}

function getRunProgress(
  steps: DiagnosisRunStepView[],
  status: DiagnosisRunStatus,
): DiagnosisRunTimelineItem["progress"] {
  const latestProgress = [...steps].reverse().find((step) => step.progress)?.progress;
  if (latestProgress) {
    return latestProgress;
  }
  return {
    label: "\u6267\u884c\u8fdb\u5ea6",
    value: status === "running" ? 5 : 100,
  };
}

function dedupeRunLines(lines: string[]) {
  const seen = new Set<string>();
  const result: string[] = [];
  lines.forEach((line) => {
    const normalized = normalizeDiagnosisDisplayText(line).trim();
    if (!normalized || seen.has(normalized)) {
      return;
    }
    seen.add(normalized);
    result.push(normalized);
  });
  return result;
}

function createRunStep(item: DiagnosisSystemEventView): DiagnosisRunStepView {
  const title = getRunStepTitle(item);
  const metricLines = item.metricLines ?? extractExecutionMetricLines(item.details);
  return {
    id: `run-step-${item.id}`,
    eventKind: item.eventKind,
    stage: item.stage,
    title,
    summary: item.summary,
    details: item.details,
    timestamp: item.timestamp,
    status: getRunStepStatus(item),
    statusTone: item.statusTone,
    progress: item.progress,
    metricLines,
    toolIds: [],
  };
}

function createRunTool(
  item: Extract<DiagnosisTimelineItem, { kind: "tool" }>,
  stepId: string | undefined,
): DiagnosisRunToolView {
  return {
    id: item.id,
    toolName: item.toolName,
    params: item.params,
    timestamp: item.timestamp,
    status: item.status,
    summaryLines: item.summaryLines,
    rawResult: item.rawResult,
    stepId,
  };
}

function getRunPhaseForStep(step: DiagnosisRunStepView): DiagnosisRunPhase {
  const stage = (step.stage ?? "").toLowerCase();
  if (stage.includes("full_rollout")) {
    return "full";
  }
  if (["execution_succeeded", "execution_failed", "execution_timeout", "execution_mocked"].includes(stage)) {
    return "full";
  }
  if (
    stage.includes("canary") ||
    stage === "execution_started" ||
    stage === "approval_confirmed" ||
    stage === "observation_started"
  ) {
    return "canary";
  }
  if (step.eventKind === "canary_progress") {
    return "canary";
  }
  if (step.eventKind === "execution_progress" && step.title.includes("\u5168\u91cf")) {
    return "full";
  }
  return "single";
}

type RunSegment = {
  phase: DiagnosisRunPhase;
  steps: DiagnosisRunStepView[];
  tools: DiagnosisRunToolView[];
  firstIndex: number;
};

function splitRunSegments(
  stepItems: Array<{ index: number; step: DiagnosisRunStepView }>,
  tools: DiagnosisRunToolView[],
): RunSegment[] {
  if (stepItems.length === 0) {
    return [];
  }

  const steps = stepItems.map((item) => item.step);
  const phases = steps.map((step) => getRunPhaseForStep(step));
  const firstFullIndex = phases.findIndex((phase) => phase === "full");
  const hasCanaryPhase = phases.some((phase) => phase === "canary");

  if (firstFullIndex <= 0 || !hasCanaryPhase) {
    return [
      {
        phase: hasCanaryPhase ? "canary" : "single",
        steps,
        tools,
        firstIndex: stepItems[0]?.index ?? 0,
      },
    ];
  }

  const canaryStepItems = stepItems.slice(0, firstFullIndex);
  const fullStepItems = stepItems.slice(firstFullIndex);
  const canarySteps = canaryStepItems.map((item) => item.step);
  const fullSteps = fullStepItems.map((item) => item.step);
  const canaryStepIds = new Set(canarySteps.map((step) => step.id));
  const fullStepIds = new Set(fullSteps.map((step) => step.id));
  const canaryTools: DiagnosisRunToolView[] = [];
  const fullTools: DiagnosisRunToolView[] = [];
  const fullStartAt = new Date(fullSteps[0]?.timestamp ?? 0).getTime();

  tools.forEach((tool) => {
    if (tool.stepId && fullStepIds.has(tool.stepId)) {
      fullTools.push(tool);
      return;
    }
    if (tool.stepId && canaryStepIds.has(tool.stepId)) {
      canaryTools.push(tool);
      return;
    }

    const toolTime = new Date(tool.timestamp).getTime();
    if (Number.isFinite(toolTime) && toolTime >= fullStartAt) {
      fullTools.push(tool);
      return;
    }

    canaryTools.push(tool);
  });

  const segments: RunSegment[] = [
    {
      phase: "canary",
      steps: canarySteps,
      tools: canaryTools,
      firstIndex: canaryStepItems[0]?.index ?? 0,
    },
    {
      phase: "full",
      steps: fullSteps,
      tools: fullTools,
      firstIndex: fullStepItems[0]?.index ?? 0,
    },
  ];
  return segments.filter((segment) => segment.steps.length > 0);
}

function getRunTitle(
  phase: DiagnosisRunPhase,
  steps: DiagnosisRunStepView[],
) {
  if (phase === "canary") {
    return "\u7070\u5ea6\u89c2\u5bdf";
  }
  if (phase === "full") {
    return "\u5168\u91cf\u4fee\u590d";
  }
  return steps.some((step) => step.eventKind === "canary_progress")
    ? "\u7070\u5ea6\u6267\u884c"
    : "\u6267\u884c\u6d41\u7a0b";
}

function buildRunTimelineItem(
  runId: string,
  phase: DiagnosisRunPhase,
  steps: DiagnosisRunStepView[],
  tools: DiagnosisRunToolView[],
): DiagnosisRunTimelineItem {
  const status = getRunStatusFromSteps(steps);
  const latestStep = steps[steps.length - 1];
  const startedAt =
    steps[0]?.timestamp ?? tools[0]?.timestamp ?? new Date(0).toISOString();
  const updatedAt =
    latestStep?.timestamp ??
    tools[tools.length - 1]?.timestamp ??
    steps[0]?.timestamp ??
    new Date(0).toISOString();
  return {
    id: `execution-run-${runId}-${phase}-${steps[0]?.id ?? "0"}`,
    kind: "run",
    runId,
    phase,
    title: getRunTitle(phase, steps),
    timestamp: updatedAt,
    status,
    progress: getRunProgress(steps, status),
    currentStageLabel: latestStep?.title ?? "\u6267\u884c\u8fdb\u5ea6",
    startedAt,
    updatedAt,
    steps,
    tools,
    metrics: dedupeRunLines(steps.flatMap((step) => step.metricLines)).slice(0, 4),
  };
}

export function groupExecutionRunTimeline(
  timeline: DiagnosisTimelineItem[],
  fallbackRunId = "execution-run",
): DiagnosisTimelineItem[] {
  const runBuckets = new Map<
    string,
    { steps: Array<{ index: number; step: DiagnosisRunStepView }>; tools: DiagnosisRunToolView[] }
  >();
  const consumedIds = new Set<string>();
  let activeRunId: string | null = null;

  const getBucket = (runId: string) => {
    const existing = runBuckets.get(runId);
    if (existing) {
      return existing;
    }
    const created = { steps: [], tools: [] };
    runBuckets.set(runId, created);
    return created;
  };

  timeline.forEach((item, index) => {
    if (item.kind === "run") {
      activeRunId = item.runId;
      return;
    }

    if (isExecutionRunSystemItem(item)) {
      const runId = item.runId ?? fallbackRunId;
      const bucket = getBucket(runId);
      bucket.steps.push({ index, step: createRunStep(item) });
      consumedIds.add(item.id);
      activeRunId = runId;
      return;
    }

    if (item.kind === "tool" && activeRunId) {
      const bucket = runBuckets.get(activeRunId);
      const latestStepEntry = bucket?.steps[bucket.steps.length - 1];
      const latestStep = latestStepEntry?.step;
      if (bucket && latestStep) {
        bucket.tools.push(createRunTool(item, latestStep.id));
        latestStep.toolIds.push(item.id);
        consumedIds.add(item.id);
      }
    }
  });

  const runItemsByFirstIndex = new Map<number, DiagnosisRunTimelineItem[]>();
  for (const [runId, bucket] of runBuckets.entries()) {
    const segments = splitRunSegments(bucket.steps, bucket.tools);
    segments.forEach((segment) => {
      const runItem = buildRunTimelineItem(runId, segment.phase, segment.steps, segment.tools);
      const existing = runItemsByFirstIndex.get(segment.firstIndex) ?? [];
      existing.push(runItem);
      runItemsByFirstIndex.set(segment.firstIndex, existing);
    });
  }

  const grouped: DiagnosisTimelineItem[] = [];
  timeline.forEach((item, index) => {
    const runItems = runItemsByFirstIndex.get(index);
    if (runItems) {
      grouped.push(...runItems);
    }
    if (!consumedIds.has(item.id)) {
      grouped.push(item);
    }
  });

  return grouped;
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
        },
      });

      timelineItems.push({
        order: timelineItems.length,
        timestamp: entry.timestamp,
        item: {
          id: `trace-next-action-${index + 1}-${entry.timestamp}`,
          kind: "message",
          role: "assistant",
          content: buildTraceNextAction(entry),
          timestamp: entry.timestamp,
          label: "Next action",
        },
      });

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
        progress: record.progress,
        source: record.source,
        dedupeKey: record.dedupeKey,
        stage: record.stage,
        runId: record.runId,
        isExecutionRunEvent: record.isExecutionRunEvent,
        metricLines: record.metricLines?.map((line) => normalizeDiagnosisDisplayText(line)),
      },
    });
  });

  const sortedTimeline = [...timelineItems]
    .sort((left, right) => {
      const timeGap = new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
      if (timeGap !== 0) {
        return timeGap;
      }
      const priorityGap =
        getStreamFirstTimelinePriority(left.item) - getStreamFirstTimelinePriority(right.item);
      if (priorityGap !== 0) {
        return priorityGap;
      }
      return left.order - right.order;
    })
    .map((entry) => entry.item);
  const groupedTimeline = groupExecutionRunTimeline(
    sortedTimeline,
    `${session?.session_id ?? "diagnosis"}-execution-run`,
  );

  const latestTimelineTimestamp = sortedTimeline[sortedTimeline.length - 1]?.timestamp ?? getLatestTraceTimestamp(session);
  const candidates = buildCandidates(session);
  const hypotheses = buildHypotheses(session);
  const propagationChain = buildPropagationChain(session);
  const summary = resolveSummaryTimestamp(buildSummary(session), latestTimelineTimestamp);
  const plan = buildPlan(session);
  const reportItem = buildReportTimelineItem(
    session,
    getReportTimelineTimestamp(session, sortedTimeline),
    summary,
    candidates,
    hypotheses,
    propagationChain,
    plan,
  );
  const timeline = reportItem
    ? [...groupedTimeline, reportItem].sort((left, right) => {
        const timeGap = new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
        return timeGap;
      })
    : groupedTimeline;
  const dedupedTimeline = keepLatestSingleReportItem(timeline);

  return {
    timeline: dedupedTimeline,
    candidates,
    hypotheses,
    propagationChain,
    summary,
    plan,
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
  const reportItem = buildReportTimelineItem(
    demoSession,
    new Date(now + 3720).toISOString(),
    summary,
    candidates,
    hypotheses,
    propagationChain,
    plan,
  );

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
      ...(reportItem
        ? ([
            {
              delayMs: 3720,
              type: "append",
              item: reportItem,
            },
          ] satisfies DiagnosisDemoEvent[])
        : []),
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
