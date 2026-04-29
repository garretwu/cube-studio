import type { ChatMessage, DiagnosisResult, DiagnosisSession, Observation, SessionEvent, ThinkingStep } from "../api/types";
import { normalizeDiagnosisDisplayText } from "./diagnosisModel";
import {
  getNormalizedRootCauses,
  getPendingApprovalPlanKey,
  getPlanByPlanKey,
  getPrimaryPlan,
  getPrimaryRootCause,
} from "./rootCauseModel";

type ChipTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

type TimelineToolStatus = "loading" | "success" | "error" | "timeout";

export type DiagnosisModifiedTimelineItem =
  | {
      id: string;
      kind: "context_start";
      title: string;
      summary: string;
      details: string[];
      timestamp: string;
    }
  | {
      id: string;
      kind: "message";
      role: "user" | "assistant";
      content: string;
      timestamp: string;
      label?: string;
      sourceEventType?: string;
      sourceEventKey?: string;
    }
  | {
      id: string;
      kind: "status_sync";
      title: string;
      content: string;
      timestamp: string;
      label?: string;
    }
  | {
      id: string;
      kind: "thinking";
      title: string;
      streamingTitle?: string;
      content: string;
      timestamp: string;
      toolName?: string | null;
      status: "thinking" | "completed";
      thoughtDurationSec?: number;
      thoughtKey?: string | null;
      roundId?: string | null;
      phase?: "streaming" | "completed";
      summaryLine?: string;
    }
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

export type DiagnosisModifiedCandidateView = {
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

export type DiagnosisModifiedHypothesisView = {
  id: string;
  description: string;
  statusLabel: string;
  statusTone: ChipTone;
  evidenceForCount: number;
  evidenceAgainstCount: number;
  confidence: number;
};

export type DiagnosisModifiedPropagationStepView = {
  id: string;
  entityId: string;
  entityType: string;
  metric: string;
  valueBefore: number | string;
  valueAfter: number | string;
  description: string;
};

export type DiagnosisModifiedSummaryView = {
  title: string;
  subtitle: string;
  certaintyLabel: string;
  certaintyTone: ChipTone;
  confidenceLabel: string;
  confidenceRawLabel?: string;
  priorityLabel?: string;
  sessionLabel?: string;
  affectedServices: string[];
  impactSummary: string;
  rootCause?: string;
  rootCauseLayer?: string;
  rootCauseLayerLabel?: string;
  rootCauseEntities?: string[];
};

export type DiagnosisModifiedPlanStepView = {
  id: string;
  title: string;
  detail: string;
  toolName?: string;
  paramsSummary?: string;
  status: "done" | "pending";
};

export type DiagnosisModifiedPlanView = {
  title: string;
  description: string;
  priorityLabel: string;
  confidenceLabel: string;
  safetyLabel?: string;
  canaryLabel?: string;
  impactSummary?: string;
  steps: DiagnosisModifiedPlanStepView[];
};

export type DiagnosisModifiedLiveView = {
  timeline: DiagnosisModifiedTimelineItem[];
  candidates: DiagnosisModifiedCandidateView[];
  hypotheses?: DiagnosisModifiedHypothesisView[];
  propagationChain?: DiagnosisModifiedPropagationStepView[];
  summary?: DiagnosisModifiedSummaryView;
  plan?: DiagnosisModifiedPlanView;
};

export type DiagnosisModifiedDemoEvent =
  | {
      delayMs: number;
      type: "append";
      item: DiagnosisModifiedTimelineItem;
    }
  | {
      delayMs: number;
      type: "update_candidates";
      candidates: DiagnosisModifiedCandidateView[];
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

export type DiagnosisModifiedDemoScenario = {
  initialTimeline: DiagnosisModifiedTimelineItem[];
  events: DiagnosisModifiedDemoEvent[];
  candidates: DiagnosisModifiedCandidateView[];
  hypotheses?: DiagnosisModifiedHypothesisView[];
  propagationChain?: DiagnosisModifiedPropagationStepView[];
  summary: DiagnosisModifiedSummaryView;
  plan: DiagnosisModifiedPlanView;
  session?: DiagnosisSession;
};

function isThinkingStep(entry: ThinkingStep | Observation): entry is ThinkingStep {
  return "thought" in entry;
}

const ANSI_ESCAPE_PATTERN = /\u001b\[[0-?]*[ -/]*[@-~]/g;
const UNICODE_FORMAT_CHARS_PATTERN = /[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF]/g;
const TOOL_CALL_BLOCK_PATTERN = /\[TOOL_CALL\][\s\S]*?\[\/TOOL_CALL\]/gi;
const TOOL_CALL_TOKEN_PATTERN = /\[\/?TOOL_CALL\]/gi;
const HYPOTHESIS_PROMPT_NOISE_SNIPPETS = [
  "query=diagnose the operational issue",
  "available read-only tools",
  "prioritize deterministic service->pod->node->gpu",
  "must complete minimum ttft coverage",
];

function stripControlCharacters(text: string) {
  return text
    .replace(ANSI_ESCAPE_PATTERN, "")
    .replace(UNICODE_FORMAT_CHARS_PATTERN, "")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "");
}

export function normalizeDiagnosisModifiedDisplayText(text?: string | null) {
  return stripControlCharacters(normalizeDiagnosisDisplayText(text ?? ""))
    .replace(TOOL_CALL_BLOCK_PATTERN, " ")
    .replace(TOOL_CALL_TOKEN_PATTERN, " ")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function isPromptNoiseParagraph(paragraph: string) {
  const normalized = paragraph.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  return HYPOTHESIS_PROMPT_NOISE_SNIPPETS.some((snippet) => normalized.includes(snippet));
}

export function sanitizeHypothesisSummaryForDisplay(text?: string | null) {
  const normalized = normalizeDiagnosisModifiedDisplayText(text);
  if (!normalized) {
    return "";
  }

  const paragraphs = normalized
    .split(/\n{2,}/)
    .map((entry) => entry.trim())
    .filter(Boolean);

  if (paragraphs.length === 0) {
    return "";
  }

  const filteredParagraphs = paragraphs.filter((paragraph) => !isPromptNoiseParagraph(paragraph));
  if (filteredParagraphs.length > 0) {
    return filteredParagraphs.join("\n\n").trim();
  }

  const lines = normalized.split(/\n+/).map((entry) => entry.trim()).filter(Boolean);
  const filteredLines = lines.filter((line) => !isPromptNoiseParagraph(line));
  return filteredLines.join("\n").trim();
}

function formatValue(value: unknown): string {
  if (value == null) {
    return "-";
  }

  if (typeof value === "string") {
    const normalized = normalizeDiagnosisModifiedDisplayText(value);
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
      return "\u8f83\u5927\u6982\u7387";
    case "ambiguous":
      return "\u8bc1\u636e\u4e0d\u8db3";
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

function buildSummary(session: DiagnosisSession | undefined): DiagnosisModifiedSummaryView | undefined {
  /**
   * Build summary view from diagnosis result with primary root-cause focus.
   *
   * Purpose:
   * - provide stable headline summary while surfacing multi-root-cause count.
   * Input/Output:
   * - input: optional diagnosis session;
   * - output: summary view or undefined.
   * Compatibility rationale:
   * - reads only normalized `root_cause[]` and no removed legacy fields.
   * Why:
   * - summary should highlight primary root cause but not hide concurrent root causes.
   */
  const result = session?.diagnosis_result;
  if (!result) {
    return session
      ? {
          title: "\u6682\u65e0\u7ed3\u8bba",
          subtitle: "\u5f53\u524d\u4f1a\u8bdd\u5c1a\u672a\u5f62\u6210\u6700\u7ec8\u8bca\u65ad\u7ed3\u8bba\u3002",
          certaintyLabel: "\u5206\u6790\u4e2d",
          certaintyTone: "info",
          confidenceLabel: "--",
          confidenceRawLabel: "--",
          priorityLabel: undefined,
          sessionLabel: session.session_id,
          affectedServices: [],
          impactSummary: "\u7b49\u5f85\u63a8\u7406\u601d\u8003\u8f68\u8ff9\u4e0e\u5de5\u5177\u89c2\u5bdf\u7ed3\u679c\u3002",
          rootCause: undefined,
          rootCauseLayer: undefined,
          rootCauseLayerLabel: "\u5c42\u7ea7",
          rootCauseEntities: [],
        }
      : undefined;
  }

  const rootCauses = getNormalizedRootCauses(result);
  const primary = getPrimaryRootCause(result);
  return {
    title: "\u8bca\u65ad\u7ed3\u8bba",
    subtitle: `\u57fa\u4e8e\u73b0\u6709\u8bc1\u636e\u6574\u7406\u51fa\u7684\u5f53\u524d\u7ed3\u8bba\uff08\u5171 ${rootCauses.length} \u4e2a\u6839\u56e0\u9879\uff09\u3002`,
    certaintyLabel: getCertaintyLabel(result.diagnosis_certainty),
    certaintyTone: getCertaintyTone(result.diagnosis_certainty),
    confidenceLabel: getConfidenceLabel(result.confidence),
    confidenceRawLabel: getConfidenceRawLabel(result.confidence),
    priorityLabel: normalizeDiagnosisModifiedDisplayText(result.triage_priority ?? ""),
    sessionLabel: session?.session_id,
    affectedServices: (result.affected_services ?? []).map((item) => normalizeDiagnosisModifiedDisplayText(item)),
    impactSummary: normalizeDiagnosisModifiedDisplayText(result.impact_summary),
    rootCause: primary ? normalizeDiagnosisModifiedDisplayText(primary.title) : undefined,
    rootCauseLayer: primary?.layer,
    rootCauseLayerLabel: getLayerLabel(primary?.layer),
    rootCauseEntities: (primary?.entities ?? []).map((item) => normalizeDiagnosisModifiedDisplayText(item)),
  };
}

function buildCandidates(session: DiagnosisSession | undefined): DiagnosisModifiedCandidateView[] {
  /**
   * Build multi-root-cause cards from formal root-cause array.
   *
   * Purpose:
   * - render candidate cards as official diagnosis root-cause items.
   * Input/Output:
   * - input: optional diagnosis session;
   * - output: candidate card list.
   * Compatibility rationale:
   * - does not read removed `ranked_candidates`.
   * Why:
   * - `root_cause[]` now represents official concurrent root causes.
   */
  const result = session?.diagnosis_result;
  if (!result) {
    return [];
  }
  const rootCauses = getNormalizedRootCauses(result);
  return rootCauses.map((rootCause, index) => {
    const isPrimary = index === 0;
    return {
      id: rootCause.id || `root-cause-${index + 1}`,
      title: normalizeDiagnosisModifiedDisplayText(rootCause.title),
      summary: normalizeDiagnosisModifiedDisplayText(rootCause.impact_summary || rootCause.evidence_summary),
      confidence: rootCause.confidence,
      confidenceLabel: getConfidenceLabel(rootCause.confidence),
      statusLabel: getCertaintyLabel(isPrimary ? result.diagnosis_certainty : rootCause.certainty),
      statusTone: getCertaintyTone(isPrimary ? result.diagnosis_certainty : rootCause.certainty),
      evidenceFor: [normalizeDiagnosisModifiedDisplayText(rootCause.evidence_summary)],
      evidenceAgainst: [],
      layer: rootCause.layer,
      entities: rootCause.entities.map((item) => normalizeDiagnosisModifiedDisplayText(item)),
      rank: index + 1,
      evidenceSummary: normalizeDiagnosisModifiedDisplayText(rootCause.evidence_summary),
      distinguishingVerification: rootCause.distinguishing_verification
        ? normalizeDiagnosisModifiedDisplayText(rootCause.distinguishing_verification)
        : null,
      isPrimary,
    };
  });
}

function buildHypotheses(session: DiagnosisSession | undefined): DiagnosisModifiedHypothesisView[] {
  const hypotheses = session?.diagnosis_result?.hypotheses ?? [];
  return hypotheses.map((item, index) => ({
    id: `hypothesis-view-${index + 1}-${item.description}`,
    description: normalizeDiagnosisModifiedDisplayText(item.description),
    statusLabel: getHypothesisLabel(item.status),
    statusTone: getHypothesisTone(item.status),
    evidenceForCount: item.evidence_for.length,
    evidenceAgainstCount: item.evidence_against.length,
    confidence: item.confidence,
  }));
}

function buildPropagationChain(session: DiagnosisSession | undefined): DiagnosisModifiedPropagationStepView[] {
  const steps = session?.diagnosis_result?.propagation_chain ?? [];
  return steps.map((step, index) => ({
    id: `propagation-${index + 1}-${step.entity_id}`,
    entityId: normalizeDiagnosisModifiedDisplayText(step.entity_id),
    entityType: normalizeDiagnosisModifiedDisplayText(step.entity_type),
    metric: normalizeDiagnosisModifiedDisplayText(step.metric),
    valueBefore: step.value_before,
    valueAfter: step.value_after,
    description: normalizeDiagnosisModifiedDisplayText(step.description),
  }));
}

function buildPlan(session: DiagnosisSession | undefined, events: SessionEvent[] = []): DiagnosisModifiedPlanView | undefined {
  /**
   * Build plan view from top-level plan or primary root-cause plan fallback.
   *
   * Purpose:
   * - keep existing single-plan UX while diagnosis supports multiple root causes.
   * Input/Output:
   * - input: optional diagnosis session;
   * - output: plan view or undefined.
   * Compatibility rationale:
   * - intentionally reads only top-level and primary-root-cause plan.
   * Why:
   * - multi-plan orchestration is postponed to a separate phase.
   */
  const pendingPlanKey = getPendingApprovalPlanKey(events, session?.diagnosis_result);
  const plan =
    getPlanByPlanKey(session?.diagnosis_result, pendingPlanKey) ??
    getPrimaryPlan(session?.diagnosis_result);
  if (!plan) {
    return undefined;
  }

  const isResolved = ["resolved", "closed"].includes(session?.status ?? "");

  return {
    title: normalizeDiagnosisModifiedDisplayText(plan.root_cause),
    description: normalizeDiagnosisModifiedDisplayText(plan.description),
    priorityLabel: normalizeDiagnosisModifiedDisplayText(plan.priority),
    confidenceLabel: getConfidenceLabel(plan.confidence),
    safetyLabel: normalizeDiagnosisModifiedDisplayText(plan.safety_level),
    canaryLabel:
      plan.canary?.enabled ? `\u91d1\u4e1d\u96c0 ${plan.canary.target_percentage}% | \u89c2\u6d4b ${plan.canary.monitor_duration}m` : undefined,
    impactSummary: normalizeDiagnosisModifiedDisplayText(plan.estimated_impact),
    steps: plan.steps.map((step, index) => ({
      id: `${plan.plan_id}-${step.step_id}-${index}`,
      title: normalizeDiagnosisModifiedDisplayText(step.description),
      detail: `${step.tool} | \u8017\u65f6 ${step.timeout}s | \u6821\u9a8c ${step.verification.method}`,
      toolName: step.tool,
      paramsSummary: Object.keys(step.params).length > 0 ? formatParamsSummary(step.params) : undefined,
      status: isResolved && index === 0 ? "done" : "pending",
    })),
  };
}

function extractDiagnosisNextAction(result: DiagnosisSession["diagnosis_result"] | null | undefined) {
  if (!result || typeof result !== "object") {
    return undefined;
  }

  // `next_action` comes from backend DiagnosisResult (LangGraph reason/final output).
  const raw = (result as Record<string, unknown>).next_action;
  if (typeof raw !== "string") {
    return undefined;
  }

  const normalized = normalizeDiagnosisModifiedDisplayText(raw).trim();
  return normalized.length > 0 ? normalized : undefined;
}

function resolveMessageMetadataField(
  metadata: ChatMessage["metadata"] | undefined,
  key: string,
): string | undefined {
  if (!metadata) {
    return undefined;
  }
  const value = metadata[key];
  if (typeof value !== "string") {
    return undefined;
  }
  const normalized = normalizeDiagnosisModifiedDisplayText(value).trim();
  return normalized.length > 0 ? normalized : undefined;
}

export function buildDiagnosisModifiedLiveView(
  session: DiagnosisSession | undefined,
  messages: ChatMessage[],
  events: SessionEvent[] = [],
): DiagnosisModifiedLiveView {
  const timelineItems: DiagnosisModifiedTimelineItem[] = [];
  const traceEntries = session?.trace?.steps ?? [];
  const pendingTools: Array<Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }>> = [];

  for (let index = 0; index < traceEntries.length; index += 1) {
    const entry = traceEntries[index];
    if (!entry) {
      continue;
    }

    if (isThinkingStep(entry)) {
      const normalizedThoughtKey = normalizeDiagnosisModifiedDisplayText(entry.thought_key).trim();
      timelineItems.push({
        id: normalizedThoughtKey
          ? `trace-thinking-${normalizedThoughtKey}`
          : `trace-thinking-${index + 1}-${entry.timestamp}`,
        kind: "thinking",
        title:
          entry.action_type === "tool_call"
            ? "Agent is planning a tool call"
            : entry.action_type === "conclude"
              ? "Agent is converging on the diagnosis"
              : "Agent is expanding diagnostic context",
        content: normalizeDiagnosisModifiedDisplayText(entry.thought),
        timestamp: entry.timestamp,
        toolName: entry.tool_name,
        status: "completed",
        thoughtKey: entry.thought_key ?? null,
        roundId: null,
        phase: "completed",
      });

      if (entry.action_type === "tool_call" && entry.tool_name) {
        const toolItem: Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> = {
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
        timelineItems.push(toolItem);
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
      id: `trace-orphan-tool-${index + 1}-${entry.timestamp}`,
      kind: "tool",
      toolName: entry.tool,
      params: entry.params,
      timestamp: entry.timestamp,
      status: "success",
      summaryLines: summarizeResult(entry.result),
      rawResult: entry.result,
    });
  }

  messages.forEach((message, index) => {
    if (message.role === "assistant" && message.display?.thinking_raw) {
      timelineItems.push({
        id: `chat-thinking-${message.id}`,
        kind: "thinking",
        title: "Pre-answer reasoning",
        content: normalizeDiagnosisModifiedDisplayText(message.display.thinking_raw),
        timestamp: message.created_at,
        status: "completed",
        phase: "completed",
      });
    }

    if (message.role === "tool") {
      timelineItems.push({
        id: `chat-tool-${message.id}`,
        kind: "tool",
        toolName: message.tool_name ?? "tool_call",
        params: {},
        timestamp: message.created_at,
        status: "success",
        summaryLines: [normalizeDiagnosisModifiedDisplayText(message.content)],
      });
      return;
    }

    timelineItems.push({
      id: `chat-message-${message.id}-${index}`,
      kind: "message",
      role: message.role === "user" ? "user" : "assistant",
      content: normalizeDiagnosisModifiedDisplayText(message.display?.answer ?? message.content),
      timestamp: message.created_at,
      label: message.role === "user" ? "User input" : "修复状态更新",
      sourceEventType: resolveMessageMetadataField(message.metadata, "event_type"),
      sourceEventKey: resolveMessageMetadataField(message.metadata, "event_key"),
    });
  });

  const diagnosisNextAction = extractDiagnosisNextAction(session?.diagnosis_result);
  if (diagnosisNextAction) {
    const fallbackTimestamp =
      traceEntries[traceEntries.length - 1]?.timestamp ??
      session?.alert.starts_at ??
      new Date().toISOString();
    const nextActionSourceId = session?.session_id?.trim() || "current";

    timelineItems.push({
      id: `diagnosis-result-next-action-${nextActionSourceId}`,
      kind: "message",
      role: "assistant",
      content: diagnosisNextAction,
      timestamp: fallbackTimestamp,
      label: "Next action",
    });
  }

  return {
    // Keep backend trace insertion order as source-of-truth sequence.
    timeline: timelineItems,
    candidates: buildCandidates(session),
    hypotheses: buildHypotheses(session),
    propagationChain: buildPropagationChain(session),
    summary: buildSummary(session),
    plan: buildPlan(session, events),
  };
}

function extractServiceName(prompt: string) {
  const matched = /([a-z0-9-]+(?:svc|service))/i.exec(prompt);
  return matched?.[1] ?? "auth-svc";
}

export function buildDiagnosisModifiedDemoScenario(prompt: string): DiagnosisModifiedDemoScenario {
  const now = Date.now();
  const serviceName = extractServiceName(prompt);
  const thinkingOneId = `demo-thinking-1-${now}`;
  const thinkingTwoId = `demo-thinking-2-${now}`;
  const thinkingThreeId = `demo-thinking-3-${now}`;
  const toolOneId = `demo-tool-metrics-${now}`;
  const toolTwoId = `demo-tool-logs-${now}`;
  const toolThreeId = `demo-tool-deploy-${now}`;

  const diagnosisResult: DiagnosisResult = {
    root_cause: [
      {
        id: "rc-1",
        title: "worker-03 \u8282\u70b9 GPU \u4e89\u7528",
        layer: "platform",
        entities: ["node:worker-03", "gpu:0"],
        confidence: 0.91,
        certainty: "confirmed",
        status: "confirmed",
        evidence_summary: "GPU util \u6301\u7eed 99%\uff0c\u8bf7\u6c42\u6392\u961f\u65f6\u957f\u4e0e\u8d85\u65f6\u544a\u8b66\u540c\u6b65\u51fa\u73b0\u3002",
        impact_summary: "vLLM p95 \u5ef6\u8fdf\u6301\u7eed\u8d70\u9ad8\uff0c\u63a8\u7406\u541e\u5410\u51fa\u73b0\u660e\u663e\u4e0b\u964d\u3002",
        distinguishing_verification: "\u786e\u8ba4 worker-03 \u7684 GPU \u5f02\u5e38\u5360\u7528\u662f\u5426\u6765\u81ea\u989d\u5916\u8d1f\u8f7d\u6216\u8d44\u6e90\u51b2\u7a81\u3002",
        recommended_fix: null,
      },
      {
        id: "rc-2",
        title: "ECN \u914d\u7f6e\u4e0d\u4e00\u81f4",
        layer: "network",
        entities: ["switch:tor-07"],
        confidence: 0.62,
        certainty: "probable",
        status: "contributing",
        evidence_summary: "ECN \u6ce2\u52a8\u5b58\u5728\u95f4\u6b47\u6027\u5f02\u5e38\uff0c\u4f46\u4e0d\u8db3\u4ee5\u89e3\u91ca\u5f53\u524d\u5168\u91cf\u75c7\u72b6\u3002",
        impact_summary: "网络抖动对延迟有叠加影响，但非主导因素。",
        distinguishing_verification: "\u68c0\u67e5\u4ea4\u6362\u673a ECN \u914d\u7f6e\u5dee\u5f02\uff0c\u91cd\u70b9\u6bd4\u5bf9\u5f02\u5e38 TOR\u3002",
        recommended_fix: null,
      },
    ],
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
      title: "\u8bca\u65ad\u7ed3\u8bba",
      subtitle: "\u57fa\u4e8e\u73b0\u6709\u8bc1\u636e\u6574\u7406\u51fa\u7684\u5f53\u524d\u7ed3\u8bba\u3002",
      certaintyLabel: "\u5206\u6790\u4e2d",
      certaintyTone: "info",
      confidenceLabel: "--",
      affectedServices: [],
      impactSummary: "\u7b49\u5f85\u63a8\u7406\u601d\u8003\u8f68\u8ff9\u4e0e\u5de5\u5177\u89c2\u5bdf\u7ed3\u679c\u3002",
    } satisfies DiagnosisModifiedSummaryView);

  const candidates = buildCandidates(demoSession);
  const hypotheses = buildHypotheses(demoSession);
  const propagationChain = buildPropagationChain(demoSession);
  const topologyContext = {
    roots: ["node:worker-03"],
    affected_count: 2,
    affected_entities: [
      { id: "gpu:0", name: "GPU 0" },
      { id: `service:${serviceName}`, name: serviceName },
    ],
    summary: `topology blast radius: worker-03 impacts ${serviceName}`,
  };

  const initialCandidates: DiagnosisModifiedCandidateView[] = candidates.map((candidate) => {
    const normalizedConfidence = Number.isFinite(candidate.confidence)
      ? Math.max(0, Math.min(1, candidate.confidence))
      : 0;
    const seeded = Math.max(0.18, Math.min(0.72, normalizedConfidence * 0.55));
    return {
      ...candidate,
      confidence: seeded,
      confidenceLabel: `${Math.round(seeded * 100)}%`,
      isPrimary: false,
      statusLabel: `候选 ${candidate.rank ?? "-"}`,
      statusTone: "neutral",
    };
  });

  const seededCandidates: DiagnosisModifiedCandidateView[] = initialCandidates.map((candidate) => ({
    ...candidate,
    evidenceFor: candidate.evidenceFor.slice(0, 1),
    evidenceAgainst: [],
    evidenceSummary: candidate.evidenceFor[0] ?? candidate.evidenceSummary ?? candidate.summary,
  }));

  const midCandidates: DiagnosisModifiedCandidateView[] = candidates.map((candidate, index) => {
    const seededCandidate = seededCandidates[index];
    const seededConfidence = seededCandidate?.confidence ?? candidate.confidence;
    const normalizedSeededConfidence = Number.isFinite(seededConfidence)
      ? Math.max(0, Math.min(1, seededConfidence))
      : 0;
    const normalizedConfidence = Number.isFinite(candidate.confidence)
      ? Math.max(0, Math.min(1, candidate.confidence))
      : 0;
    const midConfidence = Math.min(
      0.88,
      normalizedSeededConfidence + Math.max(0.08, (normalizedConfidence - normalizedSeededConfidence) * 0.58),
    );
    const evidenceForCount = candidate.isPrimary ? Math.min(2, candidate.evidenceFor.length) : Math.min(1, candidate.evidenceFor.length);
    const evidenceAgainstCount = candidate.isPrimary ? 0 : Math.min(1, candidate.evidenceAgainst.length);

    return {
      ...candidate,
      confidence: midConfidence,
      confidenceLabel: `${Math.round(midConfidence * 100)}%`,
      isPrimary: index === 0,
      statusLabel: index === 0 ? "当前候选" : `候选 ${candidate.rank ?? index + 1}`,
      statusTone: index === 0 ? "accent" : "neutral",
      evidenceFor: candidate.evidenceFor.slice(0, evidenceForCount),
      evidenceAgainst: candidate.evidenceAgainst.slice(0, evidenceAgainstCount),
      evidenceSummary:
        candidate.evidenceFor.slice(0, evidenceForCount).at(-1) ??
        candidate.evidenceAgainst.slice(0, evidenceAgainstCount).at(-1) ??
        candidate.evidenceSummary ??
        candidate.summary,
    };
  });

  const plan =
    buildPlan(demoSession) ??
    ({
      title: "\u8bca\u65ad\u4fee\u590d\u5efa\u8bae",
      description: "\u5f85\u751f\u6210\u540e\u5c55\u793a\u53ef\u6267\u884c\u65b9\u6848\u3002",
      priorityLabel: diagnosisResult.triage_priority,
      confidenceLabel: getConfidenceLabel(diagnosisResult.confidence),
      steps: [],
    } satisfies DiagnosisModifiedPlanView);

  return {
    initialTimeline: [
      {
        id: `demo-user-${now}`,
        kind: "message",
        role: "user",
        content: prompt,
        timestamp: new Date(now).toISOString(),
        label: "\u7528\u6237\u8f93\u5165",
      },
    ],
    events: [
      {
        delayMs: 240,
        type: "append",
        item: {
          id: thinkingOneId,
          kind: "thinking",
          title: "\u6b63\u5728\u6784\u5efa\u4e0a\u4e0b\u6587...",
          content: `\u6b63\u5728\u805a\u5408 ${serviceName} \u7684\u544a\u8b66\u3001\u5386\u53f2\u4f1a\u8bdd\u3001\u5173\u952e\u6307\u6807\u4e0e\u6700\u8fd1\u53d8\u66f4\uff0c\u5148\u5efa\u7acb\u8bca\u65ad\u57fa\u7ebf\u518d\u8fdb\u5165\u6839\u56e0\u5206\u6790\u3002`,
          timestamp: new Date(now + 240).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 560,
        type: "update_thinking",
        targetId: thinkingOneId,
        status: "completed",
      },
      {
        delayMs: 700,
        type: "append",
        item: {
          id: `demo-assistant-context-summary-${now}`,
          kind: "message",
          role: "assistant",
          content: `\u4e0a\u4e0b\u6587\u6784\u5efa\u5b8c\u6210\uff1a\u672c\u6b21\u5f02\u5e38\u96c6\u4e2d\u5728 ${serviceName} \u63a8\u7406\u94fe\u8def\uff0c\u544a\u8b66\u7a97\u53e3\u5185\u5ef6\u8fdf\u62ac\u5347\u4e0e\u8282\u70b9\u8d44\u6e90\u5360\u7528\u540c\u6b65\u51fa\u73b0\uff0c\u4f18\u5148\u6392\u67e5\u8282\u70b9\u7ea7\u8d44\u6e90\u4e89\u7528\u3002\nTopology context:\n${JSON.stringify(topologyContext)}`,
          timestamp: new Date(now + 700).toISOString(),
          label: "\u4e0a\u4e0b\u6587\u7ed3\u8bba",
        },
      },
      {
        delayMs: 980,
        type: "append",
        item: {
          id: `demo-assistant-next-step-1-${now}`,
          kind: "message",
          role: "assistant",
          content: `\u63a5\u4e0b\u6765\u5148\u62c9\u53d6 ${serviceName} \u7684\u5173\u952e\u6307\u6807\uff0c\u786e\u8ba4\u5ef6\u8fdf\u4e0e\u8d44\u6e90\u4f7f\u7528\u662f\u5426\u540c\u5411\u5f02\u5e38\uff0c\u518d\u5224\u65ad\u662f\u5426\u4e3a\u8282\u70b9\u7ea7\u95ee\u9898\u3002`,
          timestamp: new Date(now + 980).toISOString(),
          label: "\u4e0b\u4e00\u6b65\u884c\u52a8",
        },
      },
      {
        delayMs: 1120,
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
          timestamp: new Date(now + 1120).toISOString(),
          status: "loading",
          summaryLines: ["\u62c9\u53d6\u6700\u8fd1 30 \u5206\u949f\u5173\u952e\u6307\u6807..."],
        },
      },
      {
        delayMs: 1480,
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
        delayMs: 1520,
        type: "update_candidates",
        candidates: seededCandidates,
      },
      {
        delayMs: 1660,
        type: "append",
        item: {
          id: thinkingTwoId,
          kind: "thinking",
          title: "\u7f29\u5c0f\u5019\u9009\u8def\u5f84",
          content: "GPU \u4e89\u7528\u4e0e\u5ef6\u8fdf\u540c\u6b65\u62ac\u5347\uff0c\u9700\u9a8c\u8bc1\u662f\u5426\u5b58\u5728\u8282\u70b9\u7ea7\u5f02\u5e38\uff0c\u540c\u65f6\u6392\u9664\u7f51\u7edc\u5c42\u548c\u6d41\u91cf\u7a81\u589e\u3002",
          timestamp: new Date(now + 1660).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 1800,
        type: "append",
        item: {
          id: `demo-assistant-next-step-2-${now}`,
          kind: "message",
          role: "assistant",
          content: "\u4e0b\u4e00\u6b65\u4f1a\u6293\u53d6\u9519\u8bef\u65e5\u5fd7\uff0c\u9a8c\u8bc1\u662f\u5426\u5b58\u5728 worker-03 \u7684\u8d85\u65f6\u5f02\u5e38\u4e0e\u989d\u5916\u5360\u7528\uff0c\u518d\u51b3\u5b9a\u662f\u5426\u6267\u884c\u6d41\u91cf\u8fc1\u79fb\u3002",
          timestamp: new Date(now + 1800).toISOString(),
          label: "\u4e0b\u4e00\u6b65\u884c\u52a8",
        },
      },
      {
        delayMs: 1900,
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
          timestamp: new Date(now + 1900).toISOString(),
          status: "loading",
          summaryLines: ["\u62c9\u53d6\u9519\u8bef\u65e5\u5fd7\u5e76\u5339\u914d\u5f02\u5e38\u8282\u70b9..."],
        },
      },
      {
        delayMs: 2260,
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
        delayMs: 2320,
        type: "update_candidates",
        candidates: midCandidates,
      },
      {
        delayMs: 2460,
        type: "update_thinking",
        targetId: thinkingTwoId,
        status: "completed",
      },
      {
        delayMs: 2860,
        type: "append",
        item: {
          id: toolThreeId,
          kind: "tool",
          toolName: "get_recent_deployments",
          params: {
            service: serviceName,
            limit: 2,
          },
          timestamp: new Date(now + 2860).toISOString(),
          status: "loading",
          summaryLines: ["\u62c9\u53d6\u6700\u8fd1\u90e8\u7f72\u8bb0\u5f55..."],
        },
      },
      {
        delayMs: 3220,
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
        delayMs: 3320,
        type: "update_candidates",
        candidates,
      },
      {
        delayMs: 3620,
        type: "append",
        item: {
          id: thinkingThreeId,
          kind: "thinking",
          title: "\u5f62\u6210\u6700\u7ec8\u7ed3\u8bba",
          content: "\u8bc1\u636e\u6700\u7ec8\u6536\u655b\u5230 worker-03 GPU \u4e89\u7528\u4e0e\u5f02\u5e38\u5360\u7528\uff0c\u6392\u961f\u7b49\u5f85\u4e0e\u5ef6\u8fdf\u540c\u6b65\u62ac\u5347\uff0c\u540c\u65f6\u5df2\u53cd\u8bc1\u6392\u9664 RDMA \u6296\u52a8\u3002",
          timestamp: new Date(now + 3620).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 3860,
        type: "update_thinking",
        targetId: thinkingThreeId,
        status: "completed",
      },
      {
        delayMs: 3960,
        type: "append",
        item: {
          id: `demo-assistant-final-${now}`,
          kind: "message",
          role: "assistant",
          content: "\u5f53\u524d\u7ed3\u8bba\u4e3a worker-03 \u8282\u70b9 GPU \u4e89\u7528\u3002\u4e0b\u65b9\u5361\u7247\u5df2\u540c\u6b65\u5019\u9009\u6839\u56e0\u3001\u5173\u952e\u8bc1\u636e\u3001\u533a\u5206\u9a8c\u8bc1\u548c\u4fee\u590d\u5efa\u8bae\u3002",
          timestamp: new Date(now + 3960).toISOString(),
          label: "\u6700\u7ec8\u7ed3\u8bba",
        },
      },
      {
        delayMs: 4260,
        type: "complete",
      },
    ],
    candidates,
    hypotheses,
    propagationChain,
    summary,
    plan,
    session: demoSession,
  };
}
