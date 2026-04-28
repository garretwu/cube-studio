import type {
  DiagnosisLocalAuditRecord,
  DiagnosisSession,
  DiagnosisStartedData,
  RemediationPlan,
  SessionEvent,
} from "../api/types";
import {
  normalizeDiagnosisModifiedDisplayText,
  sanitizeHypothesisSummaryForDisplay,
} from "./diagnosisModifiedModel";
import type {
  DiagnosisModifiedCandidateView,
  DiagnosisModifiedPlanView,
  DiagnosisModifiedSummaryView,
  DiagnosisModifiedTimelineItem,
} from "./diagnosisModifiedModel";

export type ReportTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";
export type ReportSectionState = "loading" | "ready" | "empty";

export type DiagnosisModifiedStageView = {
  label: string;
  tone: ReportTone;
  detail: string;
  isActive: boolean;
};

export type DiagnosisModifiedReportFact = {
  label: string;
  value: string;
};

export type DiagnosisModifiedContextNodeView = {
  id: string;
  label: string;
  role: "problem" | "affected";
  tone: ReportTone;
  detail?: string;
};

export type DiagnosisModifiedContextEdgeView = {
  id: string;
  sourceId: string;
  targetId: string;
  label: string;
};

export type DiagnosisModifiedContextView = {
  state: ReportSectionState;
  summary: string;
  topologyEmptyReason?: "no_direct_relations";
  problemNodes: DiagnosisModifiedContextNodeView[];
  affectedNodes: DiagnosisModifiedContextNodeView[];
  graph: {
    nodes: DiagnosisModifiedContextNodeView[];
    edges: DiagnosisModifiedContextEdgeView[];
  };
};

export type DiagnosisModifiedCandidateChangeView = {
  id: string;
  title: string;
  summary: string;
  confidenceLabel: string;
  statusLabel: string;
  tone: ReportTone;
};

export type DiagnosisModifiedExecutionView = {
  title: string;
  detail: string;
  highlights: string[];
  tone: ReportTone;
};

export type DiagnosisModifiedFeedbackItem = {
  id: string;
  label: string;
  summary: string;
  detail?: string;
  tone: ReportTone;
  timestamp: string;
};

export type DiagnosisModifiedHypothesisItemView = {
  id: string;
  title: string;
  summary: string;
  description: string;
  confidenceLabel: string;
  statusLabel: string;
  tone: ReportTone;
  evidenceItems: DiagnosisModifiedHypothesisEvidenceItemView[];
  confidenceUpdates: DiagnosisModifiedConfidenceUpdateView[];
};

export type DiagnosisModifiedHypothesisEvidenceItemView = {
  id: string;
  kind: "support" | "against" | "validation";
  summary: string;
  tone: ReportTone;
};

export type DiagnosisModifiedHypothesesView = {
  state: ReportSectionState;
  summary: string;
  description: string;
  detailMode: "expanded" | "collapsed";
  items: DiagnosisModifiedHypothesisItemView[];
};

export type DiagnosisModifiedVerificationItemView = {
  id: string;
  title: string;
  summary: string;
  detail?: string;
  tone: ReportTone;
};

export type DiagnosisModifiedVerificationView = {
  state: ReportSectionState;
  summary: string;
  items: DiagnosisModifiedVerificationItemView[];
};

export type DiagnosisModifiedConfidenceUpdateView = {
  id: string;
  label: string;
  summary: string;
  timestamp: string;
  tone: ReportTone;
};

export type DiagnosisModifiedConfidenceView = {
  state: ReportSectionState;
  summary: string;
  updates: DiagnosisModifiedConfidenceUpdateView[];
};

export type DiagnosisModifiedRemediationStepView = {
  id: string;
  title: string;
  detail: string;
  statusLabel: string;
};

export type DiagnosisModifiedNextActionView = {
  mode: "idle" | "diagnosing" | "approval" | "executing" | "resolved" | "rejected";
  title: string;
  description: string;
  helper?: string;
};

export type DiagnosisModifiedRootCauseView = {
  state: ReportSectionState;
  summary: string;
  items: DiagnosisModifiedRootCauseItemView[];
};

export type DiagnosisModifiedRootCauseItemView = {
  id: string;
  title: string;
  summary: string;
  facts: DiagnosisModifiedReportFact[];
  remediation: DiagnosisModifiedRemediationKeyView;
  isPrimary: boolean;
  rankLabel?: string;
};

export type DiagnosisModifiedProgressStageId =
  | "context"
  | "hypotheses"
  | "verification"
  | "confidence"
  | "remediation";

export type DiagnosisModifiedProgressStepView = {
  id: DiagnosisModifiedProgressStageId;
  title: string;
  summary: string;
  status: "completed" | "active" | "pending";
};

export type DiagnosisModifiedProgressView = {
  activeStepId: DiagnosisModifiedProgressStageId;
  steps: DiagnosisModifiedProgressStepView[];
};

export type DiagnosisModifiedRemediationKeyView = {
  state: ReportSectionState;
  title: string;
  detail: string;
  facts: DiagnosisModifiedReportFact[];
  steps: DiagnosisModifiedRemediationStepView[];
};

export type DiagnosisModifiedReportView = {
  overview: {
    eyebrow: string;
    title: string;
    subtitle: string;
    isDefaultPreview: boolean;
    sessionId?: string;
    alertName: string;
    service?: string;
    severityLabel?: string;
    updatedAt?: string;
    status: DiagnosisModifiedStageView;
    meta: string[];
    badges: Array<{ label: string; tone: ReportTone }>;
  };
  context: DiagnosisModifiedContextView;
  progress: DiagnosisModifiedProgressView;
  hypotheses: DiagnosisModifiedHypothesesView;
  verification: DiagnosisModifiedVerificationView;
  confidence: DiagnosisModifiedConfidenceView;
  conclusion: {
    title: string;
    summary: string;
    facts: DiagnosisModifiedReportFact[];
  };
  rootCause: DiagnosisModifiedRootCauseView;
  rootCauseReady: boolean;
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
  stage: DiagnosisModifiedStageView;
  execution: DiagnosisModifiedExecutionView;
  feedback: DiagnosisModifiedFeedbackItem[];
  remediation: DiagnosisModifiedRemediationKeyView;
  nextAction: DiagnosisModifiedNextActionView;
};

export type BuildDiagnosisModifiedReportViewInput = {
  session?: DiagnosisSession;
  timeline: DiagnosisModifiedTimelineItem[];
  topologyContext?: DiagnosisStartedData["topology"] | null;
  relationScope?: "direct_only";
  candidates?: DiagnosisModifiedCandidateView[];
  candidateSnapshots?: Array<{
    id: string;
    timestamp: string;
    candidates: DiagnosisModifiedCandidateView[];
  }>;
  summary?: DiagnosisModifiedSummaryView;
  plan?: DiagnosisModifiedPlanView;
  events?: SessionEvent[];
  localAuditRecords?: DiagnosisLocalAuditRecord[];
};

type UnifiedRecord = {
  id: string;
  eventKind:
    | "approval_result"
    | "canary_progress"
    | "execution_progress"
    | "metric_feedback"
    | "alert_recovery"
    | "session_closed";
  label: string;
  summary: string;
  detail?: string;
  tone: ReportTone;
  timestamp: string;
  progress?: {
    label: string;
    value: number;
    helper?: string;
  };
};

const LAYER_LABELS: Record<string, string> = {
  hardware: "硬件",
  network: "网络",
  os: "操作系统",
  platform: "平台",
  service: "服务",
};

const TERMINAL_STATUSES = new Set(["resolved", "closed", "failed", "timeout", "escalated", "rejected"]);
const DIRECT_ONLY_FALLBACK_MAX_NEIGHBORS = 8;

function normalizeText(value?: string | null) {
  return normalizeDiagnosisModifiedDisplayText(String(value ?? "").replace(/^\[系统\]\s*/u, ""));
}

type ParsedTopologyContext = {
  roots: string[];
  affected_count?: number;
  filtered_affected_count?: number;
  raw_affected_count?: number;
  raw_affected_total_count?: number;
  filter_policy?: string;
  dropped_count?: number;
  mandatory_kept?: Record<string, number>;
  dropped_after_mandatory_budget?: number;
  dropped_by_policy?: Record<string, number>;
  affected_entities?: Array<{ id?: string; name?: string } & Record<string, unknown>>;
  summary?: string;
  direct_relations?: Array<{
    source: string;
    target: string;
    target_type: string;
    target_name: string;
    relation: string;
    direction: "in" | "out";
  }>;
};

function formatConfidence(value: number | undefined) {
  const safeValue = Math.max(0, Math.min(1, value ?? 0));
  return `${Math.round(safeValue * 100)}%`;
}

function formatDuration(seconds?: number) {
  const safeSeconds = Math.max(0, Math.round(seconds ?? 0));
  if (safeSeconds >= 3600) {
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  }
  if (safeSeconds >= 60) {
    const minutes = Math.floor(safeSeconds / 60);
    const remainSeconds = safeSeconds % 60;
    return remainSeconds > 0 ? `${minutes}m ${remainSeconds}s` : `${minutes}m`;
  }
  return `${safeSeconds}s`;
}

function formatSeverity(value?: string | null) {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "critical") return "严重";
  if (normalized === "warning") return "警告";
  if (normalized === "info") return "提示";
  return value ? String(value) : "--";
}

function formatLayer(value?: string | null) {
  const normalized = String(value ?? "").trim().toLowerCase();
  return LAYER_LABELS[normalized] ?? (value ? String(value) : "--");
}

function uniqueStrings(values: Array<string | null | undefined>) {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach((value) => {
    const text = String(value ?? "").trim();
    if (!text || seen.has(text)) {
      return;
    }
    seen.add(text);
    result.push(text);
  });
  return result;
}

function getEntityLabel(value: string) {
  const trimmed = value.trim();
  const parts = trimmed.split(":").filter(Boolean);
  return parts[parts.length - 1] ?? trimmed;
}

function getContextNodeId(value: string) {
  return normalizeContextEntity(value);
}

function sanitizeId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "node";
}

export function mapDiagnosisModifiedStage(status?: string | null): DiagnosisModifiedStageView {
  const normalized = String(status ?? "").trim().toLowerCase();
  switch (normalized) {
    case "":
    case "idle":
    case "not_started":
    case "pending":
      return { label: "未开始", tone: "neutral", detail: "诊断尚未开始，报告框架已就绪。", isActive: false };
    case "diagnosing":
      return { label: "诊断中", tone: "info", detail: "正在收集证据并归纳根因结论。", isActive: true };
    case "diagnosed":
      return { label: "已诊断", tone: "accent", detail: "诊断结论已形成，等待修复方案继续推进。", isActive: true };
    case "approval_required":
    case "awaiting_approval":
      return { label: "待审批", tone: "info", detail: "修复方案已生成，等待人工确认。", isActive: true };
    case "approved":
      return { label: "已批准", tone: "accent", detail: "方案已确认，等待执行链路推进。", isActive: true };
    case "remediating":
      return { label: "修复中", tone: "warning", detail: "系统正在按照当前方案执行修复动作。", isActive: true };
    case "validating":
      return { label: "验证中", tone: "warning", detail: "正在观察指标与告警恢复情况。", isActive: true };
    case "resolved":
      return { label: "已解决", tone: "success", detail: "关键症状已恢复，等待收口。", isActive: false };
    case "closed":
      return { label: "已关闭", tone: "neutral", detail: "本次诊断修复流程已结束。", isActive: false };
    case "failed":
      return { label: "失败", tone: "danger", detail: "当前修复或验证链路失败，需要继续处理。", isActive: false };
    case "timeout":
      return { label: "超时", tone: "danger", detail: "当前修复或验证链路超时，需要继续处理。", isActive: false };
    case "escalated":
      return { label: "已升级", tone: "danger", detail: "当前会话已升级到人工或更高层级处理。", isActive: false };
    case "rejected":
      return { label: "已驳回", tone: "danger", detail: "当前方案未被接受，需要继续调整。", isActive: false };
    default:
      return { label: "处理中", tone: "neutral", detail: "当前状态尚未归类。", isActive: true };
  }
}

function buildFallbackCandidates(session?: DiagnosisSession) {
  return (session?.diagnosis_result?.ranked_candidates ?? []).map((candidate) => ({
    id: `ranked-${candidate.rank}-${candidate.root_cause}`,
    title: normalizeText(candidate.root_cause),
    summary: normalizeText(candidate.evidence_summary),
    confidenceLabel: formatConfidence(candidate.confidence),
    statusLabel: candidate.rank === 1 ? "当前根因" : `候选 ${candidate.rank}`,
    tone: candidate.rank === 1 ? "accent" : "neutral",
  } satisfies DiagnosisModifiedCandidateChangeView));
}

function buildCandidateChanges(input: BuildDiagnosisModifiedReportViewInput) {
  const primaryRootCause = normalizeText(input.summary?.rootCause ?? input.session?.diagnosis_result?.root_cause ?? "");
  const explicitCandidates = (input.candidates ?? []).map((candidate, index) => ({
    id: candidate.id,
    title: normalizeText(candidate.title),
    summary: normalizeText(candidate.evidenceSummary ?? candidate.summary),
    confidenceLabel: candidate.confidenceLabel,
    statusLabel:
      candidate.isPrimary || normalizeText(candidate.title) === primaryRootCause
        ? "当前根因"
        : `候选 ${candidate.rank ?? index + 1}`,
    tone:
      candidate.isPrimary || normalizeText(candidate.title) === primaryRootCause
        ? "accent"
        : candidate.statusTone,
  } satisfies DiagnosisModifiedCandidateChangeView));

  const rows = explicitCandidates.length > 0 ? explicitCandidates : buildFallbackCandidates(input.session);
  return rows.slice(0, 3);
}

function getCandidateSnapshots(input: BuildDiagnosisModifiedReportViewInput) {
  if (input.candidateSnapshots && input.candidateSnapshots.length > 0) {
    return input.candidateSnapshots
      .filter((snapshot) => snapshot.candidates.length > 0)
      .map((snapshot) => ({
        id: snapshot.id,
        timestamp: snapshot.timestamp,
        candidates: snapshot.candidates.slice(0, 3),
      }));
  }

  if ((input.candidates ?? []).length > 0) {
    const latestTimestamp =
      [...input.timeline.map((item) => item.timestamp)].sort((left, right) => timestampValue(right) - timestampValue(left))[0] ??
      new Date(0).toISOString();
    return [
      {
        id: "candidate-snapshot-current",
        timestamp: latestTimestamp,
        candidates: (input.candidates ?? []).slice(0, 3),
      },
    ];
  }

  return [];
}

function getHypothesisStatusLabel(candidate: DiagnosisModifiedCandidateView, index: number) {
  if (candidate.isPrimary) {
    return "当前根因";
  }
  if (candidate.statusLabel?.trim()) {
    return candidate.statusLabel;
  }
  return `候选 ${candidate.rank ?? index + 1}`;
}

function getHypothesisTone(candidate: DiagnosisModifiedCandidateView): ReportTone {
  if (candidate.isPrimary) {
    return "accent";
  }
  if (candidate.statusTone === "success") {
    return "success";
  }
  if (candidate.statusTone === "warning" || candidate.statusTone === "danger") {
    return "warning";
  }
  return "neutral";
}

function buildHypotheses(input: BuildDiagnosisModifiedReportViewInput): DiagnosisModifiedHypothesesView {
  const snapshots = getCandidateSnapshots(input);
  const latestSnapshot = snapshots.at(-1);
  const detailMode = hasRootCauseConclusion(input) ? "collapsed" : "expanded";

  if (!latestSnapshot) {
    return {
      state: "loading",
      summary: "Waiting for candidate root-cause selection.",
      description: "Each shortlisted hypothesis will include concise evidence and confidence movement once candidates appear.",
      detailMode,
      items: [],
    };
  }

  const sectionSummary = `已选中 ${latestSnapshot.candidates.length} 个候选假设。`;
  const sectionDescription =
    detailMode === "collapsed"
      ? "结论已趋于稳定，默认折叠细节；可按候选展开查看证据与置信度变化。"
      : "每个候选假设展示证据与置信度变化，帮助快速定位当前最可信路径。";

  return {
    state: "ready",
    summary: sectionSummary,
    description: sectionDescription,
    detailMode,
    items: latestSnapshot.candidates.map((candidate, index) => ({
      id: candidate.id,
      title: candidate.title,
      summary: sanitizeHypothesisSummaryForDisplay(candidate.evidenceSummary ?? candidate.summary),
      description: buildHypothesisDescription(candidate, index),
      confidenceLabel: candidate.confidenceLabel,
      statusLabel: getHypothesisStatusLabel(candidate, index),
      tone: getHypothesisTone(candidate),
      evidenceItems: buildHypothesisEvidenceItems(candidate),
      confidenceUpdates: buildHypothesisConfidenceUpdates(candidate, snapshots),
    })),
  };
}

function buildHypothesisDescription(candidate: DiagnosisModifiedCandidateView, index: number) {
  const statusLabel = getHypothesisStatusLabel(candidate, index);
  const entities = uniqueStrings(candidate.entities).map((entity) => getEntityLabel(entity));
  const segments: string[] = [`状态: ${statusLabel}`, `置信度: ${candidate.confidenceLabel}`];

  if (entities.length > 0) {
    segments.push(`关联实体: ${entities.join(", ")}`);
  }

  const verification = normalizeText(candidate.distinguishingVerification);
  if (verification) {
    segments.push(`区分验证: ${verification}`);
  }

  return segments.join(" | ");
}

function buildHypothesisEvidenceItems(
  candidate: DiagnosisModifiedCandidateView,
): DiagnosisModifiedHypothesisEvidenceItemView[] {
  const supportItems = candidate.evidenceFor
    .map((summary, index) => ({
      id: `${candidate.id}-support-${index + 1}`,
      kind: "support" as const,
      summary: sanitizeHypothesisSummaryForDisplay(summary),
      tone: "success" as const,
    }))
    .filter((item) => item.summary.length > 0);

  const againstItems = candidate.evidenceAgainst
    .map((summary, index) => ({
      id: `${candidate.id}-against-${index + 1}`,
      kind: "against" as const,
      summary: sanitizeHypothesisSummaryForDisplay(summary),
      tone: "warning" as const,
    }))
    .filter((item) => item.summary.length > 0);

  const validationItems =
    candidate.distinguishingVerification && candidate.distinguishingVerification.trim().length > 0
      ? (() => {
          const sanitized = sanitizeHypothesisSummaryForDisplay(candidate.distinguishingVerification);
          if (!sanitized) {
            return [];
          }
          return [
            {
              id: `${candidate.id}-validation-next`,
              kind: "validation" as const,
              summary: sanitized,
              tone: "info" as const,
            },
          ];
        })()
      : [];

  return [...supportItems, ...againstItems, ...validationItems];
}

function buildHypothesisConfidenceUpdates(
  candidate: DiagnosisModifiedCandidateView,
  snapshots: ReturnType<typeof getCandidateSnapshots>,
): DiagnosisModifiedConfidenceUpdateView[] {
  const history = snapshots
    .map((snapshot) => ({
      timestamp: snapshot.timestamp,
      candidate: snapshot.candidates.find((item) => item.title === candidate.title),
    }))
    .filter(
      (entry): entry is { timestamp: string; candidate: DiagnosisModifiedCandidateView } => Boolean(entry.candidate),
    );

  return history.map(({ candidate: currentCandidate, timestamp }, index) => {
    const previousCandidate = index > 0 ? history[index - 1]?.candidate : undefined;
    const previousConfidence = previousCandidate?.confidenceLabel;
    const currentConfidence = currentCandidate.confidenceLabel;

    let tone: ReportTone = "info";
    if (previousCandidate) {
      if ((currentCandidate.confidence ?? 0) > (previousCandidate.confidence ?? 0)) {
        tone = "accent";
      } else if ((currentCandidate.confidence ?? 0) < (previousCandidate.confidence ?? 0)) {
        tone = "warning";
      } else {
        tone = "neutral";
      }
    } else if (currentCandidate.isPrimary) {
      tone = "accent";
    }

    return {
      id: `${candidate.id}-confidence-${index + 1}`,
      label: index === 0 ? "Initial ranking" : "Confidence update",
      summary: previousConfidence
        ? `${candidate.title} moved from ${previousConfidence} to ${currentConfidence}.`
        : `${candidate.title} entered the shortlist at ${currentConfidence}.`,
      timestamp,
      tone,
    } satisfies DiagnosisModifiedConfidenceUpdateView;
  });
}

function buildVerification(input: BuildDiagnosisModifiedReportViewInput): DiagnosisModifiedVerificationView {
  const toolItems = input.timeline
    .filter(
      (item): item is Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> =>
        item.kind === "tool" && item.status === "success",
    )
    .slice(-3)
    .map((item) => ({
      id: item.id,
      title: item.toolName,
      summary: item.summaryLines[0] ?? "Validation completed.",
      detail: item.summaryLines.slice(1).join(" | ") || undefined,
      tone: "info" as const,
    }));

  const nextActionItems = input.timeline
    .filter(
      (item): item is Extract<DiagnosisModifiedTimelineItem, { kind: "message" }> =>
        item.kind === "message" &&
        item.role === "assistant" &&
        ((item.label?.trim() ?? "") === "Next action" || item.content.startsWith("Next action:")),
    )
    .slice(-2)
    .map((item) => ({
      id: item.id,
      title: "Next validation",
      summary: item.content.replace(/^Next action:\s*/i, ""),
      tone: "neutral" as const,
    }));

  const items = [...toolItems, ...nextActionItems];

  if (items.length === 0) {
    return {
      state: "loading",
      summary: "Waiting for discriminative validation evidence.",
      items: [],
    };
  }

  return {
    state: "ready",
    summary: `Captured ${items.length} verification checkpoints from the active trace.`,
    items,
  };
}

function buildConfidence(input: BuildDiagnosisModifiedReportViewInput): DiagnosisModifiedConfidenceView {
  const snapshots = getCandidateSnapshots(input);
  const resultConfidence = input.summary?.confidenceRawLabel ?? input.session?.diagnosis_result?.confidence;

  if (snapshots.length === 0) {
    if (resultConfidence == null) {
      return {
        state: "loading",
        summary: "Waiting for confidence movement.",
        updates: [],
      };
    }

    return {
      state: "ready",
      summary: "The report has a settled confidence score.",
      updates: [
        {
          id: "confidence-final",
          label: "Current confidence",
          summary: `Current root-cause confidence is ${typeof resultConfidence === "number" ? formatConfidence(resultConfidence) : resultConfidence}.`,
          timestamp:
            [...input.timeline.map((item) => item.timestamp)].sort((left, right) => timestampValue(right) - timestampValue(left))[0] ??
            new Date(0).toISOString(),
          tone: "accent",
        },
      ],
    };
  }

  if (snapshots.length < 2 && resultConfidence == null) {
    return {
      state: "loading",
      summary: "Baseline hypothesis is ready; confidence will appear after additional verification.",
      updates: [],
    };
  }

  const updates = snapshots.map((snapshot, index) => {
    const topCandidate = snapshot.candidates[0];
    const previousTopCandidate = index > 0 ? snapshots[index - 1]?.candidates[0] : undefined;
    const previousConfidence = previousTopCandidate?.title === topCandidate?.title ? previousTopCandidate.confidenceLabel : undefined;
    const summary = topCandidate
      ? previousConfidence
        ? `${topCandidate.title} moved from ${previousConfidence} to ${topCandidate.confidenceLabel}.`
        : `${topCandidate.title} entered the shortlist at ${topCandidate.confidenceLabel}.`
      : "Confidence snapshot recorded.";

    return {
      id: snapshot.id,
      label: index === 0 ? "Initial ranking" : "Confidence update",
      summary,
      timestamp: snapshot.timestamp,
      tone: index === snapshots.length - 1 ? "accent" : "info",
    } satisfies DiagnosisModifiedConfidenceUpdateView;
  });

  if (resultConfidence != null) {
    updates.push({
      id: "confidence-final",
      label: "Current confidence",
      summary: `Current root-cause confidence is ${
        typeof resultConfidence === "number" ? formatConfidence(resultConfidence) : resultConfidence
      }.`,
      timestamp:
        [...input.timeline.map((item) => item.timestamp)].sort((left, right) => timestampValue(right) - timestampValue(left))[0] ??
        new Date(0).toISOString(),
      tone: "accent",
    });
  }

  return {
    state: "ready",
    summary: `Tracked ${updates.length} confidence updates across the diagnosis trace.`,
    updates,
  };
}

function buildProgress(args: {
  context: DiagnosisModifiedContextView;
  hypotheses: DiagnosisModifiedHypothesesView;
  verification: DiagnosisModifiedVerificationView;
  confidence: DiagnosisModifiedConfidenceView;
  remediation: DiagnosisModifiedRemediationKeyView;
  rootCauseReady: boolean;
}): DiagnosisModifiedProgressView {
  const started = {
    context:
      args.context.state === "ready" ||
      args.hypotheses.state === "ready" ||
      args.verification.state === "ready" ||
      args.confidence.state === "ready" ||
      args.remediation.state === "ready",
    hypotheses:
      args.hypotheses.state === "ready" ||
      args.verification.state === "ready" ||
      args.confidence.state === "ready" ||
      args.remediation.state === "ready" ||
      args.rootCauseReady,
    verification:
      args.verification.state === "ready" ||
      args.confidence.state === "ready" ||
      args.remediation.state === "ready" ||
      args.rootCauseReady,
    confidence: args.confidence.state === "ready" || args.remediation.state === "ready" || args.rootCauseReady,
    remediation: args.remediation.state === "ready",
  } as const;

  const order: DiagnosisModifiedProgressStageId[] = [
    "context",
    "hypotheses",
    "verification",
    "confidence",
    "remediation",
  ];
  const activeStepId =
    [...order].reverse().find((id) => started[id]) ?? "context";
  const activeIndex = order.indexOf(activeStepId);

  const summaries: Record<DiagnosisModifiedProgressStageId, string> = {
    context:
      args.context.state === "ready"
        ? args.context.summary
        : "Waiting for topology and blast-radius context.",
    hypotheses:
      args.hypotheses.state === "ready"
        ? args.hypotheses.summary
        : "Waiting for the first selected hypotheses.",
    verification:
      args.verification.state === "ready"
        ? args.verification.summary
        : "Waiting for validation evidence from tools and follow-up checks.",
    confidence:
      args.confidence.state === "ready"
        ? args.confidence.summary
        : "Waiting for confidence movement before locking the report.",
    remediation:
      args.remediation.state === "ready"
        ? "High-confidence remediation planning is now available."
        : "Remediation will open after the root cause is stable enough to act on.",
  };
  const titles: Record<DiagnosisModifiedProgressStageId, string> = {
    context: "Impact topology",
    hypotheses: "Selected hypotheses",
    verification: "Verification trail",
    confidence: "Confidence updates",
    remediation: "Remediation generation",
  };

  return {
    activeStepId,
    steps: order.map((id, index) => ({
      id,
      title: titles[id],
      summary: summaries[id],
      status:
        index < activeIndex
          ? "completed"
          : index === activeIndex
            ? "active"
            : "pending",
    })),
  };
}

function isParsedTopologyContext(value: unknown): value is ParsedTopologyContext {
  if (!value || typeof value !== "object") {
    return false;
  }
  const record = value as Record<string, unknown>;
  if (!Array.isArray(record.roots)) {
    return false;
  }
  const rootsOk = record.roots.every((item) => typeof item === "string");
  if (!rootsOk) {
    return false;
  }
  if (record.affected_entities != null && !Array.isArray(record.affected_entities)) {
    return false;
  }
  return true;
}

function extractJsonObjectAfterMarker(text: string, marker: string) {
  const index = text.indexOf(marker);
  if (index < 0) {
    return null;
  }
  const start = text.indexOf("{", index + marker.length);
  if (start < 0) {
    return null;
  }
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (ch === "\\") {
      escape = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) {
      continue;
    }
    if (ch === "{") {
      depth += 1;
      continue;
    }
    if (ch === "}") {
      depth -= 1;
      if (depth === 0) {
        return text.slice(start, i + 1);
      }
    }
  }
  return null;
}

function extractJsonObjects(text: string) {
  const results: string[] = [];
  let depth = 0;
  let inString = false;
  let escape = false;
  let start = -1;

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (ch === "\\") {
      escape = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) {
      continue;
    }
    if (ch === "{") {
      if (depth === 0) {
        start = i;
      }
      depth += 1;
      continue;
    }
    if (ch === "}") {
      if (depth === 0) {
        continue;
      }
      depth -= 1;
      if (depth === 0 && start >= 0) {
        results.push(text.slice(start, i + 1));
        start = -1;
      }
    }
  }

  return results;
}

function parseTopologyContextCandidate(value: unknown, depth = 0): ParsedTopologyContext | null {
  if (depth > 2 || value == null) {
    return null;
  }

  if (typeof value === "string") {
    try {
      return parseTopologyContextCandidate(JSON.parse(value) as unknown, depth + 1);
    } catch {
      return null;
    }
  }

  if (isParsedTopologyContext(value)) {
    return value;
  }

  if (typeof value !== "object") {
    return null;
  }

  const record = value as Record<string, unknown>;
  const nestedCandidates = [
    record.topology_context,
    record.topologyContext,
    record.context,
    record.data,
    record.result,
    record.payload,
  ];

  for (const candidate of nestedCandidates) {
    const parsed = parseTopologyContextCandidate(candidate, depth + 1);
    if (parsed) {
      return parsed;
    }
  }

  return null;
}

function parseTopologyContextFromContent(content: string): ParsedTopologyContext | null {
  const markers = [
    "Topology context:",
    "topology context:",
    "拓扑上下文:",
    "拓扑上下文：",
    "topology_context",
  ];

  for (const marker of markers) {
    if (!content.includes(marker)) {
      continue;
    }
    const jsonText = extractJsonObjectAfterMarker(content, marker);
    if (!jsonText) {
      continue;
    }
    const parsed = parseTopologyContextCandidate(jsonText);
    if (parsed) {
      return parsed;
    }
  }

  const allJsonObjects = extractJsonObjects(content);
  for (const jsonText of allJsonObjects) {
    const parsed = parseTopologyContextCandidate(jsonText);
    if (parsed) {
      return parsed;
    }
  }

  return null;
}

function parseTopologyContextFromTimeline(timeline: DiagnosisModifiedTimelineItem[]): ParsedTopologyContext | null {
  for (let i = timeline.length - 1; i >= 0; i -= 1) {
    const item = timeline[i];
    if (!item) {
      continue;
    }

    if (item.kind === "message") {
      const parsedFromMessage = parseTopologyContextFromContent(item.content);
      if (parsedFromMessage) {
        return parsedFromMessage;
      }
      continue;
    }

    if (item.kind === "tool") {
      const parsedFromTool = parseTopologyContextCandidate(item.rawResult);
      if (parsedFromTool) {
        return parsedFromTool;
      }
    }
  }
  return null;
}

function normalizeContextEntity(value: string) {
  const text = normalizeText(value);
  if (!text) {
    return "";
  }
  if (text.includes(":")) {
    return text;
  }
  return `entity:${text}`;
}

function inferEntityKind(entity: string) {
  const normalized = normalizeContextEntity(entity).toLowerCase();
  if (normalized.startsWith("pod:") || normalized.includes("pod")) {
    return "pod";
  }
  if (normalized.startsWith("node:") || normalized.includes("node")) {
    return "node";
  }
  if (normalized.startsWith("gpu:") || normalized.includes("gpu")) {
    return "gpu";
  }
  if (normalized.startsWith("bmc:") || normalized.includes("bmc")) {
    return "bmc";
  }
  if (normalized.startsWith("proc:") || normalized.includes("process")) {
    return "process";
  }
  if (normalized.startsWith("service:") || normalized.includes("service") || normalized.includes("svc")) {
    return "service";
  }
  return "entity";
}

function normalizeContextEntityFromLabel(key: string, value: string) {
  const normalizedValue = normalizeText(value);
  if (!normalizedValue) {
    return "";
  }
  const normalizedKey = key.trim().toLowerCase();
  if (normalizedValue.includes(":")) {
    return normalizeContextEntity(normalizedValue);
  }
  if (normalizedKey === "pod" || normalizedKey === "pod_name") {
    return normalizeContextEntity(`pod:${normalizedValue}`);
  }
  if (normalizedKey === "service" || normalizedKey === "app" || normalizedKey === "deployment") {
    return normalizeContextEntity(`service:${normalizedValue}`);
  }
  if (normalizedKey === "node" || normalizedKey === "instance" || normalizedKey === "host") {
    return normalizeContextEntity(`node:${normalizedValue}`);
  }
  if (normalizedKey === "gpu" || normalizedKey === "gpu_id" || normalizedKey === "gpu_index") {
    return normalizeContextEntity(`gpu:${normalizedValue}`);
  }
  if (normalizedKey === "bmc") {
    return normalizeContextEntity(`bmc:${normalizedValue}`);
  }
  return normalizeContextEntity(normalizedValue);
}

function extractAlertSubjectEntity(
  input: BuildDiagnosisModifiedReportViewInput,
  parsedTopology: ParsedTopologyContext | null,
  normalizedAlertName: string,
) {
  const labels = input.session?.alert?.labels ?? {};
  if (labels.source_entity) {
    const normalizedSource = normalizeContextEntityFromLabel("source_entity", labels.source_entity);
    if (normalizedSource) {
      return normalizedSource;
    }
  }
  const topologyRoot = parsedTopology?.roots?.find((item) => normalizeContextEntity(item));
  if (topologyRoot) {
    return normalizeContextEntity(topologyRoot);
  }
  const preferredKeys = isGpuTemperatureAlert(normalizedAlertName)
    ? [
        "gpu",
        "gpu_id",
        "gpu_index",
        "node",
        "instance",
        "host",
        "bmc",
        "pod",
        "pod_name",
        "service",
        "app",
        "deployment",
      ]
    : isTtftAlert(normalizedAlertName)
      ? [
          "pod",
          "pod_name",
          "service",
          "app",
          "deployment",
          "node",
          "instance",
          "host",
          "gpu",
          "gpu_id",
          "gpu_index",
          "bmc",
        ]
      : [
          "pod",
          "pod_name",
          "service",
          "app",
          "deployment",
          "gpu",
          "gpu_id",
          "gpu_index",
          "node",
          "instance",
          "host",
          "bmc",
        ];
  for (const key of preferredKeys) {
    const rawValue = labels[key];
    if (!rawValue) {
      continue;
    }
    const normalized = normalizeContextEntityFromLabel(key, rawValue);
    if (normalized) {
      return normalized;
    }
  }
  const resultEntities = input.summary?.rootCauseEntities ?? getResult(input)?.root_cause_entities ?? [];
  const firstResultEntity = resultEntities.find((item) => normalizeContextEntity(item));
  if (firstResultEntity) {
    return normalizeContextEntity(firstResultEntity);
  }

  return "";
}

function collectTopologyEntities(parsedTopology: ParsedTopologyContext | null) {
  if (!parsedTopology) {
    return [];
  }
  const roots = (parsedTopology.roots ?? []).map((root) => normalizeContextEntity(root));
  const affected =
    parsedTopology.affected_entities?.map((entity) => {
      const id = typeof entity?.id === "string" && entity.id.trim() ? entity.id.trim() : "";
      const name = typeof entity?.name === "string" && entity.name.trim() ? entity.name.trim() : "";
      return normalizeContextEntity(id || name);
    }) ?? [];
  return uniqueStrings([...roots, ...affected]);
}

function isTtftAlert(alertName: string) {
  return alertName.includes("ttft");
}

function isGpuTemperatureAlert(alertName: string) {
  return alertName.includes("gpu") || alertName.includes("temperature") || alertName.includes("温度");
}

function shouldKeepDirectNeighbor(alertName: string, subjectKind: string, neighborKind: string) {
  if (isTtftAlert(alertName)) {
    void subjectKind;
    return true;
  }
  if (isGpuTemperatureAlert(alertName)) {
    if (subjectKind === "gpu") {
      return neighborKind === "node" || neighborKind === "bmc";
    }
    if (subjectKind === "node") {
      return neighborKind === "gpu" || neighborKind === "bmc";
    }
    if (subjectKind === "bmc") {
      return neighborKind === "node" || neighborKind === "gpu";
    }
    return neighborKind === "gpu" || neighborKind === "node" || neighborKind === "bmc";
  }
  return true;
}

function getTtftNeighborPriority(neighborKind: string) {
  if (neighborKind === "service" || neighborKind === "pod" || neighborKind === "node") {
    return 0;
  }
  if (neighborKind === "gpu") {
    return 1;
  }
  if (neighborKind === "process" || neighborKind === "bmc") {
    return 2;
  }
  return 3;
}

function getDirectRelationLabel(alertName: string, subjectKind: string, neighborKind: string) {
  if (isTtftAlert(alertName)) {
    if (subjectKind === "service" && neighborKind === "pod") {
      return "关联实例";
    }
    if (subjectKind === "pod" && neighborKind === "service") {
      return "隶属服务";
    }
    if ((subjectKind === "service" || subjectKind === "pod") && neighborKind === "node") {
      return "运行于";
    }
    if (subjectKind === "node" && (neighborKind === "pod" || neighborKind === "service")) {
      return "承载";
    }
  }
  if (isGpuTemperatureAlert(alertName)) {
    if (subjectKind === "gpu" && neighborKind === "node") {
      return "所在节点";
    }
    if ((subjectKind === "gpu" && neighborKind === "bmc") || (subjectKind === "node" && neighborKind === "bmc")) {
      return "硬件管理";
    }
    if (subjectKind === "node" && neighborKind === "gpu") {
      return "承载GPU";
    }
    if (subjectKind === "bmc" && neighborKind === "node") {
      return "管理节点";
    }
    if (subjectKind === "bmc" && neighborKind === "gpu") {
      return "管理硬件";
    }
  }
  return "关联";
}

function translateRelationLabel(relation: string): string {
  const normalized = relation.trim().toLowerCase();
  switch (normalized) {
    case "runs_on":
    case "hosted_on":
      return "运行于";
    case "part_of":
    case "serves":
      return "隶属";
    case "connected_to":
      return "连接";
    case "depends_on":
      return "依赖";
    case "manages":
    case "monitored_by":
      return "管理";
    default:
      return "关联";
  }
}

function buildDirectOnlyFallbackNeighbors({
  parsedTopology,
  subjectNode,
  existingNodeIds,
}: {
  parsedTopology: ParsedTopologyContext | null;
  subjectNode: DiagnosisModifiedContextNodeView;
  existingNodeIds: Set<string>;
}): { neighbors: DiagnosisModifiedContextNodeView[]; edges: DiagnosisModifiedContextEdgeView[] } {
  const neighbors: DiagnosisModifiedContextNodeView[] = [];
  const edges: DiagnosisModifiedContextEdgeView[] = [];
  const affectedEntities = parsedTopology?.affected_entities ?? [];
  for (const entity of affectedEntities) {
    const id = typeof entity?.id === "string" && entity.id.trim() ? entity.id.trim() : "";
    const name = typeof entity?.name === "string" && entity.name.trim() ? entity.name.trim() : "";
    const rawEntity = id || name;
    if (!rawEntity) {
      continue;
    }
    const normalizedEntity = normalizeContextEntity(rawEntity);
    const targetId = getContextNodeId(normalizedEntity);
    if (!targetId || targetId === subjectNode.id || existingNodeIds.has(targetId)) {
      continue;
    }
    existingNodeIds.add(targetId);
    neighbors.push({
      id: targetId,
      label: name || getEntityLabel(normalizedEntity),
      role: "affected",
      tone: "warning",
      detail: normalizedEntity,
    });
    edges.push({
      id: `context-edge-${sanitizeId(subjectNode.id)}-${sanitizeId(targetId)}-fallback`,
      sourceId: subjectNode.id,
      targetId,
      label: "关联",
    });
    if (neighbors.length >= DIRECT_ONLY_FALLBACK_MAX_NEIGHBORS) {
      break;
    }
  }
  return { neighbors, edges };
}

function buildDirectOnlyContext(input: BuildDiagnosisModifiedReportViewInput): DiagnosisModifiedContextView {
  const parsedTopology = parseTopologyContextCandidate(input.topologyContext);
  const normalizedAlertName = normalizeText(input.session?.alert.alert_name).toLowerCase();
  const subjectEntity = extractAlertSubjectEntity(input, parsedTopology, normalizedAlertName);
  if (!subjectEntity) {
    return {
      state: "loading",
      summary: "等待诊断上下文生成。",
      topologyEmptyReason: "no_direct_relations",
      problemNodes: [],
      affectedNodes: [],
      graph: { nodes: [], edges: [] },
    };
  }

  const subjectNode: DiagnosisModifiedContextNodeView = {
    id: getContextNodeId(subjectEntity),
    label: getEntityLabel(subjectEntity),
    role: "problem",
    tone: "danger",
    detail: subjectEntity,
  };

  const directRelations = parsedTopology?.direct_relations;
  if (directRelations && directRelations.length > 0) {
    const neighbors: DiagnosisModifiedContextNodeView[] = [];
    const edges: DiagnosisModifiedContextEdgeView[] = [];
    const seenIds = new Set<string>();

    for (const rel of directRelations) {
      const targetId = getContextNodeId(rel.target);
      if (!targetId || targetId === subjectNode.id || seenIds.has(targetId)) continue;
      seenIds.add(targetId);

      neighbors.push({
        id: targetId,
        label: rel.target_name || getEntityLabel(rel.target),
        role: "affected",
        tone: "warning",
        detail: rel.target,
      });
      edges.push({
        id: `context-edge-${sanitizeId(subjectNode.id)}-${sanitizeId(targetId)}-${sanitizeId(rel.relation)}`,
        sourceId: subjectNode.id,
        targetId,
        label: translateRelationLabel(rel.relation),
      });
    }

    if (edges.length === 0 && (parsedTopology?.affected_entities?.length ?? 0) > 0) {
      const fallback = buildDirectOnlyFallbackNeighbors({
        parsedTopology,
        subjectNode,
        existingNodeIds: seenIds,
      });
      neighbors.push(...fallback.neighbors);
      edges.push(...fallback.edges);
    }

    const hasRelations = edges.length > 0;
    const summary =
      typeof parsedTopology?.summary === "string" && normalizeText(parsedTopology.summary) && hasRelations
        ? normalizeText(parsedTopology.summary)
        : hasRelations
          ? "展示告警主体的直连关联实体。"
          : "暂无告警主体的直连关联实体。";

    return {
      state: "ready",
      summary,
      topologyEmptyReason: hasRelations ? undefined : "no_direct_relations",
      problemNodes: [subjectNode],
      affectedNodes: neighbors,
      graph: { nodes: [subjectNode, ...neighbors], edges },
    };
  }

  const subjectKind = inferEntityKind(subjectEntity);
  const allEntities = uniqueStrings([subjectEntity, ...collectTopologyEntities(parsedTopology)]);

  const directNeighbors: DiagnosisModifiedContextNodeView[] = [];
  const directEdges: DiagnosisModifiedContextEdgeView[] = [];
  const seenNeighborIds = new Set<string>();
  const candidateEntities = allEntities.filter((entity) => entity && entity !== subjectEntity);
  if (isTtftAlert(normalizedAlertName)) {
    candidateEntities.sort((left, right) => {
      const leftKind = inferEntityKind(left);
      const rightKind = inferEntityKind(right);
      const leftPriority = getTtftNeighborPriority(leftKind);
      const rightPriority = getTtftNeighborPriority(rightKind);
      if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
      }
      return left.localeCompare(right);
    });
  }

  candidateEntities.forEach((entity) => {
    if (!entity || entity === subjectEntity) {
      return;
    }
    const entityId = getContextNodeId(entity);
    if (!entityId || entityId === subjectNode.id || seenNeighborIds.has(entityId)) {
      return;
    }
    const neighborKind = inferEntityKind(entity);
    if (!shouldKeepDirectNeighbor(normalizedAlertName, subjectKind, neighborKind)) {
      return;
    }
    seenNeighborIds.add(entityId);
    directNeighbors.push({
      id: entityId,
      label: getEntityLabel(entity),
      role: "affected",
      tone: "warning",
      detail: entity,
    });
    const label = getDirectRelationLabel(normalizedAlertName, subjectKind, neighborKind);
    directEdges.push({
      id: `context-edge-${sanitizeId(subjectNode.id)}-${sanitizeId(entityId)}-${sanitizeId(label)}`,
      sourceId: subjectNode.id,
      targetId: entityId,
      label,
    });
  });

  if (directEdges.length === 0 && (parsedTopology?.affected_entities?.length ?? 0) > 0) {
    const fallback = buildDirectOnlyFallbackNeighbors({
      parsedTopology,
      subjectNode,
      existingNodeIds: seenNeighborIds,
    });
    directNeighbors.push(...fallback.neighbors);
    directEdges.push(...fallback.edges);
  }

  const hasDirectRelations = directEdges.length > 0;
  const nodes = [subjectNode, ...directNeighbors];
  const fallbackSummary = hasDirectRelations
    ? "仅展示告警主体的一跳直连关联实体。"
    : "暂无告警主体的直连关联实体。";
  const summary =
    typeof parsedTopology?.summary === "string" && normalizeText(parsedTopology.summary) && hasDirectRelations
      ? normalizeText(parsedTopology.summary)
      : fallbackSummary;

  return {
    state: "ready",
    summary,
    topologyEmptyReason: hasDirectRelations ? undefined : "no_direct_relations",
    problemNodes: [subjectNode],
    affectedNodes: directNeighbors,
    graph: {
      nodes,
      edges: directEdges,
    },
  };
}

function getProgressLabel(progress?: UnifiedRecord["progress"]) {
  if (!progress) {
    return null;
  }
  return `${progress.label} ${progress.value}%${progress.helper ? ` · ${progress.helper}` : ""}`;
}

function timestampValue(timestamp?: string | null) {
  const value = Date.parse(String(timestamp ?? ""));
  return Number.isFinite(value) ? value : 0;
}

function extractLatestUpdateTimestamp(input: BuildDiagnosisModifiedReportViewInput) {
  const timestamps = [
    ...input.timeline.map((item) => item.timestamp),
    ...(input.events ?? []).map((event) => event.timestamp),
    ...(input.localAuditRecords ?? []).map((record) => record.timestamp),
  ].filter(Boolean);

  return timestamps.sort((left, right) => timestampValue(right) - timestampValue(left))[0];
}

function formatDateMd(timestamp?: string | null) {
  const value = Date.parse(String(timestamp ?? ""));
  if (!Number.isFinite(value)) {
    return undefined;
  }
  return new Date(value).toISOString().slice(5, 10);
}

function getResult(input: BuildDiagnosisModifiedReportViewInput) {
  return input.session?.diagnosis_result;
}

function getResultNextAction(input: BuildDiagnosisModifiedReportViewInput) {
  const result = getResult(input);
  if (!result || typeof result !== "object") {
    return undefined;
  }

  const raw = (result as Record<string, unknown>).next_action;
  if (typeof raw !== "string") {
    return undefined;
  }

  const normalized = normalizeText(raw);
  return normalized.length > 0 ? normalized : undefined;
}

function hasRootCauseConclusion(input: BuildDiagnosisModifiedReportViewInput) {
  const rootCause = normalizeText(input.summary?.rootCause ?? getResult(input)?.root_cause);
  return String(rootCause ?? "").trim().length > 0;
}

function isDefaultReportPreview(input: BuildDiagnosisModifiedReportViewInput) {
  const normalizedStatus = String(input.session?.status ?? "").trim().toLowerCase();
  const result = getResult(input);
  const hasTimeline = input.timeline.length > 0;
  const hasCandidates = (input.candidates?.length ?? 0) > 0 || (input.candidateSnapshots?.length ?? 0) > 0;
  const hasSummary = Boolean(input.summary);
  const hasPlan = Boolean(input.plan);
  const hasEvents = (input.events?.length ?? 0) > 0 || (input.localAuditRecords?.length ?? 0) > 0;
  const hasDiagnosisResult = Boolean(result);
  const isIdleLikeStatus =
    normalizedStatus.length === 0 ||
    normalizedStatus === "idle" ||
    normalizedStatus === "not_started" ||
    normalizedStatus === "pending";

  return isIdleLikeStatus && !hasTimeline && !hasCandidates && !hasSummary && !hasPlan && !hasEvents && !hasDiagnosisResult;
}

function buildOverview(input: BuildDiagnosisModifiedReportViewInput, stage: DiagnosisModifiedStageView) {
  const session = input.session;
  const result = getResult(input);
  const alertName = normalizeText(session?.alert.alert_name ?? "当前告警");
  const defaultPreview = isDefaultReportPreview(input);
  const summaryTitle = normalizeText(defaultPreview ? "诊断修复报告" : input.summary?.rootCause ?? result?.root_cause ?? "诊断修复报告");
  const affectedServices = (input.summary?.affectedServices ?? result?.affected_services ?? []).map((item) => normalizeText(item));
  const primaryService =
    normalizeText(session?.alert?.labels?.service) ||
    normalizeText(session?.alert?.labels?.app) ||
    affectedServices[0] ||
    undefined;
  const latestTimestamp = defaultPreview ? undefined : extractLatestUpdateTimestamp(input);
  const reportDate = formatDateMd(latestTimestamp) ?? formatDateMd(session?.alert?.starts_at);
  const overviewTitle = defaultPreview
    ? "诊断报告"
    : [reportDate, alertName, "诊断报告"].filter((part) => Boolean(part && part.trim())).join("") || summaryTitle;
  const duration = session?.duration_seconds ? `持续 ${formatDuration(session.duration_seconds)}` : "";
  const round = session?.re_diagnosis_round ? `第 ${session.re_diagnosis_round} 轮` : "";
  const severityLabel = session?.alert?.severity ? formatSeverity(session.alert.severity) : undefined;

  return {
    eyebrow: "诊断总览",
    isDefaultPreview: defaultPreview,
    title: overviewTitle,
    subtitle:
      normalizeText(
        defaultPreview
          ? "诊断开始后，将在此持续生成结构化分析结论与修复建议"
          : result?.impact_summary ??
          input.summary?.impactSummary ??
          input.summary?.subtitle ??
          "当前报告用于持续呈现最新结论、执行状态与修复反馈。",
      ),
    sessionId: session?.session_id,
    alertName,
    service: primaryService,
    severityLabel,
    updatedAt: latestTimestamp,
    status: stage,
    meta: [
      session?.session_id ? `会话 ${session.session_id}` : "",
      alertName ? `告警 ${alertName}` : "",
      latestTimestamp ? `更新 ${latestTimestamp}` : "",
      duration,
      round,
      primaryService ? `服务 ${primaryService}` : "",
    ].filter(Boolean),
    badges: [
      { label: stage.label, tone: stage.tone },
      session?.alert?.severity ? { label: formatSeverity(session.alert.severity), tone: "warning" as const } : null,
      affectedServices.length > 0 ? { label: `${affectedServices.length} 个受影响服务`, tone: "neutral" as const } : null,
    ].filter((item): item is { label: string; tone: ReportTone } => Boolean(item)),
  };
}

function buildContext(input: BuildDiagnosisModifiedReportViewInput): DiagnosisModifiedContextView {
  const relationScope = input.relationScope ?? "direct_only";
  if (relationScope === "direct_only") {
    return buildDirectOnlyContext(input);
  }

  return {
    state: "loading",
    summary: "等待诊断上下文生成。",
    topologyEmptyReason: "no_direct_relations",
    problemNodes: [],
    affectedNodes: [],
    graph: { nodes: [], edges: [] },
  };
}

function buildConclusion(input: BuildDiagnosisModifiedReportViewInput) {
  const summary = input.summary;
  const result = getResult(input);
  const facts: DiagnosisModifiedReportFact[] = [
    {
      label: "根因",
      value: normalizeText(summary?.rootCause ?? result?.root_cause ?? "待收敛"),
    },
    {
      label: "层级",
      value: summary?.rootCauseLayerLabel ?? formatLayer(summary?.rootCauseLayer ?? result?.root_cause_layer),
    },
    {
      label: "实体",
      value: normalizeText(summary?.rootCauseEntities?.join("、") ?? result?.root_cause_entities?.join("、") ?? "--"),
    },
    {
      label: "影响",
      value: normalizeText(summary?.impactSummary ?? result?.impact_summary ?? "--"),
    },
  ];

  return {
    title: normalizeText(summary?.rootCause ?? result?.root_cause ?? "等待形成明确结论"),
    summary: normalizeText(
      result?.impact_summary ??
        summary?.impactSummary ??
        summary?.subtitle ??
        "当前尚未形成稳定的根因与影响结论。",
    ),
    facts,
  };
}

function mapEventStage(stage: string) {
  switch (stage) {
    case "execution_started":
      return { eventKind: "execution_progress" as const, label: "开始执行", tone: "warning" as ReportTone };
    case "canary_started":
      return { eventKind: "canary_progress" as const, label: "开始灰度", tone: "warning" as ReportTone };
    case "canary_progress":
    case "canary_batch_progress":
      return { eventKind: "canary_progress" as const, label: "灰度执行中", tone: "warning" as ReportTone };
    case "canary_succeeded":
    case "canary_completed":
      return { eventKind: "canary_progress" as const, label: "灰度完成", tone: "success" as ReportTone };
    case "observation_started":
    case "observation_result":
      return { eventKind: "metric_feedback" as const, label: "指标反馈", tone: "info" as ReportTone };
    case "full_rollout_started":
    case "full_rollout_progress":
      return { eventKind: "execution_progress" as const, label: "全量执行中", tone: "warning" as ReportTone };
    case "full_rollout_succeeded":
    case "execution_succeeded":
      return { eventKind: "execution_progress" as const, label: "执行完成", tone: "success" as ReportTone };
    case "execution_failed":
    case "execution_timeout":
    case "escalation_required":
      return { eventKind: "execution_progress" as const, label: "执行异常", tone: "danger" as ReportTone };
    case "alert_recovered":
      return { eventKind: "alert_recovery" as const, label: "告警恢复", tone: "success" as ReportTone };
    case "session_closed":
      return { eventKind: "session_closed" as const, label: "会话关闭", tone: "neutral" as ReportTone };
    default:
      return { eventKind: "execution_progress" as const, label: "执行更新", tone: "info" as ReportTone };
  }
}

function toUnifiedRecord(event: SessionEvent): UnifiedRecord | null {
  if (event.type !== "remediation_progress" && event.type !== "observation_result") {
    return null;
  }

  const data = event.data ?? {};
  const stage = String(data.stage ?? event.type ?? "").trim().toLowerCase();
  if (!stage) {
    return null;
  }

  const mapped = mapEventStage(stage);
  const message =
    typeof data.message === "string" && data.message.trim()
      ? data.message.trim()
      : mapped.label;
  const detail =
    typeof data.progress_label === "string" && data.progress_label.trim()
      ? data.progress_label.trim()
      : undefined;
  const progressValue = Number(data.progress ?? data.progress_percent ?? data.percentage);
  const progress = Number.isFinite(progressValue)
    ? {
        label: stage.includes("canary") ? "灰度进度" : stage.includes("full_rollout") ? "全量进度" : "执行进度",
        value: Math.max(0, Math.min(100, Math.round(progressValue))),
        helper: detail,
      }
    : undefined;

  return {
    id: `${event.type}-${event.timestamp}`,
    eventKind: mapped.eventKind,
    label: mapped.label,
    summary: message,
    detail,
    tone: mapped.tone,
    timestamp: event.timestamp,
    progress,
  };
}

function toUnifiedRecordFromAudit(record: DiagnosisLocalAuditRecord): UnifiedRecord {
  return {
    id: record.id,
    eventKind: record.eventKind,
    label:
      record.eventKind === "approval_result"
        ? "审批结论"
        : record.eventKind === "metric_feedback"
          ? "指标反馈"
          : record.eventKind === "alert_recovery"
            ? "告警恢复"
            : record.eventKind === "session_closed"
              ? "会话关闭"
              : record.eventKind === "canary_progress"
                ? "灰度进展"
                : "执行进展",
    summary: normalizeText(record.summary),
    detail: record.details[0],
    tone: record.statusTone,
    timestamp: record.timestamp,
    progress: record.progress,
  };
}

function buildUnifiedRecords(input: BuildDiagnosisModifiedReportViewInput) {
  const eventRecords = (input.events ?? [])
    .map((event) => toUnifiedRecord(event))
    .filter((record): record is UnifiedRecord => Boolean(record));
  const auditRecords = (input.localAuditRecords ?? []).map((record) => toUnifiedRecordFromAudit(record));

  return [...eventRecords, ...auditRecords].sort(
    (left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime(),
  );
}

function getPreferredRemediationPlan(input: BuildDiagnosisModifiedReportViewInput): RemediationPlan | undefined {
  const result = getResult(input);
  const rankedCandidates = [...(result?.ranked_candidates ?? [])].sort((left, right) => left.rank - right.rank);
  const normalizedRootCause = normalizeText(input.summary?.rootCause ?? result?.root_cause ?? "").toLowerCase();

  const matchedCandidatePlan = rankedCandidates.find((candidate) => {
    if (!candidate.recommended_fix || !normalizedRootCause) {
      return false;
    }

    return normalizeText(candidate.root_cause).toLowerCase() === normalizedRootCause;
  })?.recommended_fix;

  if (matchedCandidatePlan) {
    return matchedCandidatePlan;
  }

  const rankedPlan = rankedCandidates.find((candidate) => candidate.recommended_fix)?.recommended_fix;
  if (rankedPlan) {
    return rankedPlan;
  }

  return result?.recommended_fix ?? undefined;
}

function mapRemediationPlanToView(plan: RemediationPlan): DiagnosisModifiedPlanView {
  const canaryLabel = plan.canary?.enabled
    ? `灰度 ${plan.canary.target_percentage}% / 观察 ${plan.canary.monitor_duration} 分钟`
    : undefined;

  return {
    title: normalizeText(plan.root_cause),
    description: normalizeText(plan.description),
    priorityLabel: plan.priority,
    confidenceLabel: formatConfidence(plan.confidence),
    safetyLabel: plan.safety_level,
    canaryLabel,
    impactSummary: normalizeText(plan.estimated_impact),
    steps: plan.steps.map((step) => ({
      id: String(step.step_id),
      title: normalizeText(step.description),
      detail: step.tool,
      toolName: step.tool,
      paramsSummary: Object.keys(step.params ?? {}).length > 0 ? JSON.stringify(step.params) : undefined,
      status: "pending" as const,
    })),
  } satisfies DiagnosisModifiedPlanView;
}

function derivePlan(input: BuildDiagnosisModifiedReportViewInput) {
  const preferredPlan = getPreferredRemediationPlan(input);
  if (preferredPlan) {
    return mapRemediationPlanToView(preferredPlan);
  }

  if (input.plan) {
    return input.plan;
  }

  const plan = getResult(input)?.recommended_fix;
  if (!plan) {
    return undefined;
  }

  return mapRemediationPlanToView(plan);
}

function buildExecution(
  input: BuildDiagnosisModifiedReportViewInput,
  stage: DiagnosisModifiedStageView,
  unifiedRecords: UnifiedRecord[],
) {
  const plan = derivePlan(input);
  const latestExecution = unifiedRecords.find((record) =>
    ["canary_progress", "execution_progress"].includes(record.eventKind),
  );

  const highlights = [
    plan?.canaryLabel,
    plan?.steps?.length ? `${plan.steps.length} 个动作` : undefined,
    plan?.priorityLabel ? `优先级 ${plan.priorityLabel}` : undefined,
    plan?.safetyLabel ? `安全 ${plan.safetyLabel}` : undefined,
    getResult(input)?.recommended_fix?.estimated_impact
      ? `影响 ${getResult(input)?.recommended_fix?.estimated_impact}`
      : undefined,
    getProgressLabel(latestExecution?.progress),
  ].filter((item): item is string => Boolean(item));

  if (latestExecution) {
    return {
      title: latestExecution.label,
      detail: latestExecution.summary,
      highlights,
      tone: latestExecution.tone,
    } satisfies DiagnosisModifiedExecutionView;
  }

  if (plan) {
    return {
      title: stage.label === "待审批" ? "待审批执行" : "修复计划已生成",
      detail: plan.description,
      highlights,
      tone: stage.tone,
    } satisfies DiagnosisModifiedExecutionView;
  }

  return {
    title: "等待修复方案",
    detail: "当前会话尚未输出可执行的修复计划。",
    highlights,
    tone: "neutral",
  } satisfies DiagnosisModifiedExecutionView;
}

function buildFeedback(unifiedRecords: UnifiedRecord[]) {
  return unifiedRecords
    .filter((record) =>
      ["approval_result", "metric_feedback", "alert_recovery", "session_closed"].includes(record.eventKind),
    )
    .slice(0, 4)
    .map((record) => ({
      id: record.id,
      label: record.label,
      summary: record.summary,
      detail: record.detail,
      tone: record.tone,
      timestamp: record.timestamp,
    } satisfies DiagnosisModifiedFeedbackItem));
}

function buildNextAction(
  input: BuildDiagnosisModifiedReportViewInput,
  stage: DiagnosisModifiedStageView,
  execution: DiagnosisModifiedExecutionView,
) {
  const plan = derivePlan(input);
  const status = String(input.session?.status ?? "").trim().toLowerCase();
  const backendNextAction = getResultNextAction(input);

  if (!plan) {
    return {
      mode: input.session ? "diagnosing" : "idle",
      title: "等待修复方案",
      description: backendNextAction ?? "继续收集根因与影响后，系统会生成下一步修复建议。",
    } satisfies DiagnosisModifiedNextActionView;
  }

  if (status === "approval_required" || status === "awaiting_approval") {
    return {
      mode: "approval",
      title: "等待人工确认",
      description: backendNextAction ?? "当前方案已经生成，确认后会进入灰度或执行链路。",
      helper: plan.canaryLabel,
    } satisfies DiagnosisModifiedNextActionView;
  }

  if (["approved", "remediating", "validating"].includes(status)) {
    return {
      mode: "executing",
      title: "跟进执行与验证",
      description: backendNextAction ?? execution.detail,
      helper: plan.canaryLabel,
    } satisfies DiagnosisModifiedNextActionView;
  }

  if (["resolved", "closed"].includes(status)) {
    return {
      mode: "resolved",
      title: "修复已收口",
      description: "当前会话已经形成恢复结论，可以回看时间线确认关键变化。",
    } satisfies DiagnosisModifiedNextActionView;
  }

  if (status === "rejected") {
    return {
      mode: "rejected",
      title: "方案需要调整",
      description: "当前方案未被接受，请补充约束或继续诊断。",
    } satisfies DiagnosisModifiedNextActionView;
  }

  return {
    mode: "diagnosing",
    title: stage.label,
    description: backendNextAction ?? stage.detail,
    helper: plan.canaryLabel,
  } satisfies DiagnosisModifiedNextActionView;
}

function buildRemediation(
  input: BuildDiagnosisModifiedReportViewInput,
): DiagnosisModifiedRemediationKeyView {
  const plan = derivePlan(input);
  const status = String(input.session?.status ?? "").trim().toLowerCase();

  if (!plan) {
    return {
      state: TERMINAL_STATUSES.has(status) ? "empty" : "loading",
      title: "等待修复方案",
      detail: "当前还没有可展示的修复关键信息。",
      facts: [],
      steps: [],
    };
  }

  const facts: DiagnosisModifiedReportFact[] = [
    { label: "优先级", value: plan.priorityLabel || "--" },
    { label: "置信度", value: plan.confidenceLabel || "--" },
    { label: "安全级别", value: plan.safetyLabel || "--" },
    { label: "灰度策略", value: plan.canaryLabel || "--" },
  ];

  return {
    state: "ready",
    title: plan.title || plan.description || "修复方案",
    detail: plan.description || plan.impactSummary || "已生成修复方案。",
    facts,
    steps: plan.steps.map((step, index) => ({
      id: step.id,
      title: step.title,
      detail:
        "paramsSummary" in step && step.paramsSummary
          ? `${step.detail} | ${step.paramsSummary}`
          : step.detail,
      statusLabel: step.status === "done" ? "已完成" : `步骤 ${index + 1}`,
    })),
  };
}

function buildRemediationFromPlanView(plan: DiagnosisModifiedPlanView): DiagnosisModifiedRemediationKeyView {
  return {
    state: "ready",
    title: plan.title || plan.description || "Repair plan",
    detail: plan.description || plan.impactSummary || "Plan is ready.",
    facts: [
      { label: "Priority", value: plan.priorityLabel || "--" },
      { label: "Confidence", value: plan.confidenceLabel || "--" },
      { label: "Safety", value: plan.safetyLabel || "--" },
      { label: "Canary", value: plan.canaryLabel || "--" },
    ],
    steps: plan.steps.map((step, index) => ({
      id: step.id,
      title: step.title,
      detail: "paramsSummary" in step && step.paramsSummary ? `${step.detail} | ${step.paramsSummary}` : step.detail,
      statusLabel: step.status === "done" ? "Done" : `Step ${index + 1}`,
    })),
  };
}

function buildUnavailableCandidateRemediation(): DiagnosisModifiedRemediationKeyView {
  return {
    state: "empty",
    title: "Plan pending",
    detail: "No dedicated remediation plan is available for this root cause yet.",
    facts: [],
    steps: [],
  };
}

function buildRootCauseView(
  input: BuildDiagnosisModifiedReportViewInput,
  conclusion: DiagnosisModifiedReportView["conclusion"],
  remediation: DiagnosisModifiedRemediationKeyView,
): DiagnosisModifiedRootCauseView {
  const result = getResult(input);
  const rankedCandidates = [...(result?.ranked_candidates ?? [])]
    .sort((left, right) => left.rank - right.rank)
    .slice(0, 2);

  if (!hasRootCauseConclusion(input)) {
    return {
      state: "loading",
      summary: "Root cause convergence is in progress.",
      items: [],
    };
  }

  if (rankedCandidates.length === 0) {
    return {
      state: "ready",
      summary: "1 root cause is currently confirmed.",
      items: [
        {
          id: "root-cause-primary",
          title: conclusion.title,
          summary: conclusion.summary,
          facts: conclusion.facts,
          remediation,
          isPrimary: true,
          rankLabel: "Rank 1",
        },
      ],
    };
  }

  const normalizedPrimaryRootCause = normalizeText(input.summary?.rootCause ?? result?.root_cause ?? "").toLowerCase();
  const fallbackPlan = derivePlan(input);

  return {
    state: "ready",
    summary: `${rankedCandidates.length} root causes are listed by confidence (top 2).`,
    items: rankedCandidates.map((candidate, index) => {
      const candidateRootCause = normalizeText(candidate.root_cause);
      const isPrimary =
        (normalizedPrimaryRootCause.length > 0 && candidateRootCause.toLowerCase() === normalizedPrimaryRootCause) ||
        (normalizedPrimaryRootCause.length === 0 && index === 0);

      const candidatePlan = candidate.recommended_fix ? mapRemediationPlanToView(candidate.recommended_fix) : undefined;
      const candidateRemediation =
        candidatePlan
          ? buildRemediationFromPlanView(candidatePlan)
          : fallbackPlan
            ? buildRemediationFromPlanView(fallbackPlan)
            : remediation.state === "ready"
              ? remediation
            : buildUnavailableCandidateRemediation();

      return {
        id: `root-cause-${candidate.rank}-${sanitizeId(candidateRootCause || `candidate-${index + 1}`)}`,
        title: candidateRootCause || `Candidate root cause ${index + 1}`,
        summary: normalizeText(candidate.evidence_summary || conclusion.summary),
        facts: [
          { label: "Rank", value: `#${candidate.rank}` },
          { label: "Layer", value: formatLayer(candidate.root_cause_layer) },
          { label: "Entities", value: normalizeText(candidate.root_cause_entities?.join(", ") || "--") },
          { label: "Confidence", value: formatConfidence(candidate.confidence) },
        ],
        remediation: candidateRemediation,
        isPrimary,
        rankLabel: `Rank ${candidate.rank}`,
      } satisfies DiagnosisModifiedRootCauseItemView;
    }),
  };
}

export function buildDiagnosisModifiedReportView(
  input: BuildDiagnosisModifiedReportViewInput,
): DiagnosisModifiedReportView {
  const stage = mapDiagnosisModifiedStage(input.session?.status);
  const unifiedRecords = buildUnifiedRecords(input);
  const execution = buildExecution(input, stage, unifiedRecords);
  const overview = buildOverview(input, stage);
  const context = buildContext(input);
  const hypotheses = buildHypotheses(input);
  const verification = buildVerification(input);
  const confidence = buildConfidence(input);
  const remediation = buildRemediation(input);
  const conclusion = buildConclusion(input);
  const rootCause = buildRootCauseView(input, conclusion, remediation);
  const rootCauseReady = rootCause.state === "ready";
  const progress = buildProgress({
    context,
    hypotheses,
    verification,
    confidence,
    remediation,
    rootCauseReady,
  });

  return {
    overview,
    context,
    progress,
    hypotheses,
    verification,
    confidence,
    rootCause,
    rootCauseReady,
    conclusion,
    candidateChanges: buildCandidateChanges(input),
    stage,
    execution,
    feedback: buildFeedback(unifiedRecords),
    remediation,
    nextAction: buildNextAction(input, stage, execution),
  };
}
