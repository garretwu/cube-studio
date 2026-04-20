import type { DiagnosisLocalAuditRecord, DiagnosisSession, SessionEvent } from "../api/types";
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
    sessionId?: string;
    alertName: string;
    service?: string;
    updatedAt?: string;
    status: DiagnosisModifiedStageView;
    meta: string[];
    badges: Array<{ label: string; tone: ReportTone }>;
  };
  context: DiagnosisModifiedContextView;
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
  candidates?: DiagnosisModifiedCandidateView[];
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

function normalizeText(value?: string | null) {
  return String(value ?? "").replace(/^\[系统\]\s*/u, "").trim();
}

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
  return getEntityLabel(value);
}

function sanitizeId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "node";
}

export function mapDiagnosisModifiedStage(status?: string | null): DiagnosisModifiedStageView {
  const normalized = String(status ?? "").trim().toLowerCase();
  switch (normalized) {
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
    title: candidate.root_cause,
    summary: candidate.evidence_summary,
    confidenceLabel: formatConfidence(candidate.confidence),
    statusLabel: candidate.rank === 1 ? "当前根因" : `候选 ${candidate.rank}`,
    tone: candidate.rank === 1 ? "accent" : "neutral",
  } satisfies DiagnosisModifiedCandidateChangeView));
}

function buildCandidateChanges(input: BuildDiagnosisModifiedReportViewInput) {
  const primaryRootCause = input.summary?.rootCause ?? input.session?.diagnosis_result?.root_cause ?? "";
  const explicitCandidates = (input.candidates ?? []).map((candidate, index) => ({
    id: candidate.id,
    title: candidate.title,
    summary: candidate.evidenceSummary ?? candidate.summary,
    confidenceLabel: candidate.confidenceLabel,
    statusLabel:
      candidate.isPrimary || candidate.title === primaryRootCause
        ? "当前根因"
        : `候选 ${candidate.rank ?? index + 1}`,
    tone:
      candidate.isPrimary || candidate.title === primaryRootCause
        ? "accent"
        : candidate.statusTone,
  } satisfies DiagnosisModifiedCandidateChangeView));

  const rows = explicitCandidates.length > 0 ? explicitCandidates : buildFallbackCandidates(input.session);
  return rows.slice(0, 3);
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

function getResult(input: BuildDiagnosisModifiedReportViewInput) {
  return input.session?.diagnosis_result;
}

function hasRootCauseConclusion(input: BuildDiagnosisModifiedReportViewInput) {
  const rootCause = input.summary?.rootCause ?? getResult(input)?.root_cause;
  return String(rootCause ?? "").trim().length > 0;
}

function buildOverview(input: BuildDiagnosisModifiedReportViewInput, stage: DiagnosisModifiedStageView) {
  const session = input.session;
  const result = getResult(input);
  const alertName = session?.alert.alert_name ?? "当前告警";
  const summaryTitle = input.summary?.rootCause ?? result?.root_cause ?? "诊断修复报告";
  const affectedServices = input.summary?.affectedServices ?? result?.affected_services ?? [];
  const primaryService =
    session?.alert?.labels?.service ??
    session?.alert?.labels?.app ??
    affectedServices[0] ??
    undefined;
  const latestTimestamp = extractLatestUpdateTimestamp(input);
  const duration = session?.duration_seconds ? `持续 ${formatDuration(session.duration_seconds)}` : "";
  const round = session?.re_diagnosis_round ? `第 ${session.re_diagnosis_round} 轮` : "";

  return {
    eyebrow: "诊断总览",
    title: summaryTitle,
    subtitle:
      result?.impact_summary ??
      input.summary?.impactSummary ??
      input.summary?.subtitle ??
      "当前报告用于持续呈现最新结论、执行状态与修复反馈。",
    sessionId: session?.session_id,
    alertName,
    service: primaryService,
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
  const result = getResult(input);
  const problemEntities = uniqueStrings(input.summary?.rootCauseEntities ?? result?.root_cause_entities ?? []);
  const affectedServices = uniqueStrings(input.summary?.affectedServices ?? result?.affected_services ?? []);

  if (!result && problemEntities.length === 0 && affectedServices.length === 0) {
    return {
      state: "loading",
      summary: "等待诊断上下文生成。",
      problemNodes: [],
      affectedNodes: [],
      graph: { nodes: [], edges: [] },
    };
  }

  const problemNodeIds = new Set(problemEntities.map(getContextNodeId));
  const problemNodes = problemEntities.map((entity) => ({
    id: getContextNodeId(entity),
    label: getEntityLabel(entity),
    role: "problem" as const,
    tone: "danger" as const,
    detail: entity,
  }));
  const affectedNodes = affectedServices
    .filter((service) => !problemNodeIds.has(getContextNodeId(service)))
    .map((service) => ({
      id: getContextNodeId(service),
      label: getEntityLabel(service),
      role: "affected" as const,
      tone: "warning" as const,
      detail: service,
    }));

  const edges =
    problemNodes.length > 0 && affectedNodes.length > 0
      ? problemNodes.flatMap((problem) =>
          affectedNodes.map((affected) => ({
            id: `context-edge-${sanitizeId(problem.id)}-${sanitizeId(affected.id)}`,
            sourceId: problem.id,
            targetId: affected.id,
            label: "影响",
          })),
        )
      : [];
  const nodes = [...problemNodes, ...affectedNodes];

  return {
    state: nodes.length > 0 ? "ready" : "empty",
    summary:
      nodes.length > 0
        ? "基于当前根因实体和受影响服务整理诊断上下文。"
        : "当前会话尚未返回问题节点或受影响服务。",
    problemNodes,
    affectedNodes,
    graph: { nodes, edges },
  };
}

function buildConclusion(input: BuildDiagnosisModifiedReportViewInput) {
  const summary = input.summary;
  const result = getResult(input);
  const facts: DiagnosisModifiedReportFact[] = [
    {
      label: "根因",
      value: summary?.rootCause ?? result?.root_cause ?? "待收敛",
    },
    {
      label: "层级",
      value: summary?.rootCauseLayerLabel ?? formatLayer(summary?.rootCauseLayer ?? result?.root_cause_layer),
    },
    {
      label: "实体",
      value: summary?.rootCauseEntities?.join("、") ?? result?.root_cause_entities?.join("、") ?? "--",
    },
    {
      label: "影响",
      value: summary?.impactSummary ?? result?.impact_summary ?? "--",
    },
  ];

  return {
    title: summary?.rootCause ?? result?.root_cause ?? "等待形成明确结论",
    summary:
      result?.impact_summary ??
      summary?.impactSummary ??
      summary?.subtitle ??
      "当前尚未形成稳定的根因与影响结论。",
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

function derivePlan(input: BuildDiagnosisModifiedReportViewInput) {
  if (input.plan) {
    return input.plan;
  }

  const plan = getResult(input)?.recommended_fix;
  if (!plan) {
    return undefined;
  }

  const canaryLabel = plan.canary?.enabled
    ? `灰度 ${plan.canary.target_percentage}% / 观察 ${plan.canary.monitor_duration} 分钟`
    : undefined;

  return {
    title: plan.root_cause,
    description: plan.description,
    priorityLabel: plan.priority,
    confidenceLabel: formatConfidence(plan.confidence),
    safetyLabel: plan.safety_level,
    canaryLabel,
    steps: plan.steps.map((step) => ({
      id: String(step.step_id),
      title: step.description,
      detail: step.tool,
      status: "pending" as const,
    })),
  } satisfies DiagnosisModifiedPlanView;
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
    .slice(0, 3)
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

  if (!plan) {
    return {
      mode: input.session ? "diagnosing" : "idle",
      title: "等待修复方案",
      description: "继续收集根因与影响后，系统会生成下一步修复建议。",
    } satisfies DiagnosisModifiedNextActionView;
  }

  if (status === "approval_required" || status === "awaiting_approval") {
    return {
      mode: "approval",
      title: "等待人工确认",
      description: "当前方案已经生成，确认后会进入灰度或执行链路。",
      helper: plan.canaryLabel,
    } satisfies DiagnosisModifiedNextActionView;
  }

  if (["approved", "remediating", "validating"].includes(status)) {
    return {
      mode: "executing",
      title: "跟进执行与验证",
      description: execution.detail,
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
    description: stage.detail,
    helper: plan.canaryLabel,
  } satisfies DiagnosisModifiedNextActionView;
}

function buildRemediation(
  input: BuildDiagnosisModifiedReportViewInput,
  execution: DiagnosisModifiedExecutionView,
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
    title: execution.title,
    detail: execution.detail || plan.description,
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

export function buildDiagnosisModifiedReportView(
  input: BuildDiagnosisModifiedReportViewInput,
): DiagnosisModifiedReportView {
  const stage = mapDiagnosisModifiedStage(input.session?.status);
  const unifiedRecords = buildUnifiedRecords(input);
  const execution = buildExecution(input, stage, unifiedRecords);
  const overview = buildOverview(input, stage);
  const rootCauseReady = hasRootCauseConclusion(input);

  return {
    overview,
    context: buildContext(input),
    rootCause: {
      state: rootCauseReady ? "ready" : "loading",
    },
    rootCauseReady,
    conclusion: buildConclusion(input),
    candidateChanges: buildCandidateChanges(input),
    stage,
    execution,
    feedback: buildFeedback(unifiedRecords),
    remediation: buildRemediation(input, execution),
    nextAction: buildNextAction(input, stage, execution),
  };
}
