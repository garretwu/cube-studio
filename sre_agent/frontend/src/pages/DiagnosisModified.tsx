import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";

import { buildBackendWsUrl } from "../api/ws";
import type { DiagnosisLocalAuditRecord, DiagnosisSession, SessionEvent } from "../api/types";
import { AppIcon } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";
import { formatDateTimeParts, formatTimestamp } from "../utils/format";
import DiagnosisModifiedReportRail from "./DiagnosisModifiedReportRail";
import { buildDiagnosisModifiedReportView } from "./diagnosisModifiedReportModel";
import {
  buildDiagnosisModifiedDemoScenario,
  buildDiagnosisModifiedLiveView,
  normalizeDiagnosisModifiedDisplayText,
  type DiagnosisModifiedCandidateView,
  type DiagnosisModifiedDemoEvent,
  type DiagnosisModifiedPlanView,
  type DiagnosisModifiedHypothesisView,
  type DiagnosisModifiedPropagationStepView,
  type DiagnosisModifiedSummaryView,
  type DiagnosisModifiedTimelineItem,
} from "./diagnosisModifiedModel";

type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";
type ApprovalState = "pending" | "updating" | "approved" | "rejected";

type ApprovalPlanResolution = {
  label: string;
  tone: BadgeTone;
  description: string;
};

const DEMO_TEXT_SPEED_MS = 40;
const DEMO_EVENT_SLOWDOWN = 4.5;
const DEMO_MIN_TOOL_LOADING_DWELL_MS = 3500;
const TOOL_RESULT_TIMEOUT_MS = 15_000;
const STREAM_COMPLETION_BUFFER_MS = 640;
const LIVE_SESSION_RECONCILE_INTERVAL_MS = 2_000;
const DEMO_DEFAULT_PROMPT = "Analyze auth-svc latency and error-rate spike in the past hour";
const THINKING_PREVIEW_CHAR_LIMIT = 200;
const DEMO_POST_APPROVAL_AUTO_FLOW = [
  { delayMs: 280, stage: "approval_recorded" as const },
  { delayMs: 1200, stage: "canary_progress" as const },
  { delayMs: 2200, stage: "metric_feedback" as const },
  { delayMs: 3200, stage: "alert_recovery" as const },
  { delayMs: 4000, stage: "session_closed" as const },
] as const;
const TIMELINE_AUTO_FOLLOW_BOTTOM_THRESHOLD_PX = 48;
const FLOW_REMEDIATION_ENTRY_STATUSES = new Set([
  "approval_required",
  "awaiting_approval",
  "approved",
  "remediating",
  "validating",
  "resolved",
  "closed",
  "failed",
  "timeout",
  "escalated",
]);

const TERMINAL_SESSION_STATUSES = new Set([
  "resolved",
  "closed",
  "failed",
  "timeout",
  "escalated",
  "rejected",
]);
const LIVE_CONTEXT_THINKING_ID_PREFIX = "live-context-thinking-";

function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

function buildRemediationPath(sessionId: string) {
  return `/remediation?sessionId=${encodeURIComponent(sessionId)}`;
}

function getRemediationEntryLabel(status?: string) {
  switch (String(status ?? "").trim().toLowerCase()) {
    case "awaiting_approval":
    case "approval_required":
      return "\u5ba1\u6279\u4fee\u590d";
    case "approved":
    case "remediating":
    case "validating":
      return "\u67e5\u770b\u6267\u884c";
    case "resolved":
    case "closed":
    case "failed":
    case "timeout":
    case "escalated":
    case "rejected":
      return "\u67e5\u770b\u4fee\u590d\u8bb0\u5f55";
    default:
      return "\u67e5\u770b\u4fee\u590d\u6982\u89c8";
  }
}

function openRemediationPage(sessionId: string) {
  window.open(buildRemediationPath(sessionId), "_blank", "noopener,noreferrer");
}

function createDemoAuditRecord({
  details,
  eventKind,
  sessionId,
  statusTone,
  summary,
  timestamp,
}: {
  details: string[];
  eventKind: DiagnosisLocalAuditRecord["eventKind"];
  sessionId: string;
  statusTone: DiagnosisLocalAuditRecord["statusTone"];
  summary: string;
  timestamp: string;
}) {
  return {
    id: `demo-${eventKind}-${timestamp}`,
    sessionId,
    eventKind,
    source: "local_audit" as const,
    dedupeKey: `demo-${eventKind}-${sessionId}`,
    timestamp,
    summary,
    details,
    statusTone,
  } satisfies DiagnosisLocalAuditRecord;
}

function createDemoSessionEvent({
  data,
  sessionId,
  timestamp,
  type,
}: {
  data: Record<string, unknown>;
  sessionId: string;
  timestamp: string;
  type: SessionEvent["type"];
}) {
  return {
    schema_version: "1",
    type,
    session_id: sessionId,
    timestamp,
    data,
  } satisfies SessionEvent;
}

function createDemoTimelineSyncMessage({
  content,
  stage,
  timestamp,
}: {
  content: string;
  stage: string;
  timestamp: string;
}) {
  return {
    id: `demo-remediation-sync-${stage}-${timestamp}`,
    kind: "status_sync" as const,
    title: "修复状态同步",
    label: "修复状态同步",
    content,
    timestamp,
  };
}

function RemediationJumpButton({
  sessionId,
  status,
}: {
  sessionId?: string;
  status?: string;
}) {
  if (!sessionId) {
    return null;
  }

  return (
    <button
      className="diagnosis-workspace-remediation-link"
      onClick={() => openRemediationPage(sessionId)}
      type="button"
    >
      {getRemediationEntryLabel(status)}
    </button>
  );
}

function shouldShowFlowRemediationEntry(status?: string) {
  const normalizedStatus = String(status ?? "").trim().toLowerCase();
  if (normalizedStatus === "approval_required" || normalizedStatus === "awaiting_approval") {
    return false;
  }
  return FLOW_REMEDIATION_ENTRY_STATUSES.has(normalizedStatus);
}

function FlowRemediationEntry({
  sessionId,
  status,
}: {
  sessionId?: string;
  status?: string;
}) {
  if (!sessionId || !shouldShowFlowRemediationEntry(status)) {
    return null;
  }

  return (
    <div
      className="diagnosis-modified-trace-action-link"
      data-testid="diagnosis-modified-flow-remediation-entry"
    >
      <RemediationJumpButton sessionId={sessionId} status={status} />
    </div>
  );
}
const THINK_TAG_BLOCK_PATTERN = /<think>([\s\S]*?)<\/think>/i;

function estimateThoughtDurationSecFromContent(content: string) {
  return Math.max(1, Math.round((content.length * DEMO_TEXT_SPEED_MS) / 1000));
}

function formatThoughtDurationLabel(durationSec?: number) {
  void durationSec;
  return "Thought completed";
}

function splitThinkingAndConclusion(content: string): { thinking: string | null; conclusion: string } {
  const matched = THINK_TAG_BLOCK_PATTERN.exec(content);
  if (!matched) {
    return { thinking: null, conclusion: content.trim() };
  }

  const thinking = matched[1]?.trim() ?? "";
  const conclusion = content.replace(THINK_TAG_BLOCK_PATTERN, "").trim();
  return {
    thinking: thinking.length > 0 ? thinking : null,
    conclusion,
  };
}

function isDemoNarrativeCard(item: DiagnosisModifiedTimelineItem) {
  if (item.kind !== "message" || item.role !== "assistant") {
    return false;
  }

  return (
    item.id.startsWith("demo-assistant-context-summary-") ||
    item.id.startsWith("demo-assistant-next-step-") ||
    item.id.startsWith("demo-assistant-final-")
  );
}

function isTransientLiveTimelineItem(item: DiagnosisModifiedTimelineItem) {
  if (item.kind === "tool") {
    return item.status === "loading";
  }
  return false;
}

function upsertTimelineItems(
  current: DiagnosisModifiedTimelineItem[],
  incoming: DiagnosisModifiedTimelineItem[],
) {
  if (incoming.length === 0) {
    return current;
  }

  const next = [...current];
  const indexById = new Map(next.map((item, index) => [item.id, index]));
  for (const item of incoming) {
    const existingIndex = indexById.get(item.id);
    if (typeof existingIndex === "number") {
      next[existingIndex] = item;
      continue;
    }
    indexById.set(item.id, next.length);
    next.push(item);
  }
  return next;
}

function isTimelineNearBottom(container: HTMLElement, thresholdPx = TIMELINE_AUTO_FOLLOW_BOTTOM_THRESHOLD_PX) {
  const distanceToBottom = container.scrollHeight - container.clientHeight - container.scrollTop;
  return distanceToBottom <= thresholdPx;
}

function extractLatestThinkingSummary(content: string) {
  const normalized = normalizeDiagnosisModifiedDisplayText(content);
  if (!normalized) {
    return "";
  }
  const lines = normalized
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  const latest = lines.length > 0 ? lines[lines.length - 1] : normalized;
  return latest.replace(/\s+/g, " ").trim();
}

function isLowSignalThinkingFragment(fragment: string) {
  const normalized = fragment.replace(/\s+/g, "");
  if (!normalized) {
    return true;
  }
  return /^[\[\]{}"',:|]+$/.test(normalized);
}

function isStructuredThinkingLine(line: string) {
  const normalized = line.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return false;
  }

  const structuredPatternHits = [
    /(?:^|[?&])query=/i.test(normalized),
    /available read-only tools/i.test(normalized),
    /remediation_plan\s*=/i.test(normalized),
    /"\w[\w.-]*"\s*:/.test(normalized),
    /(?:^|[{,])\s*\w[\w.-]*\s*:/.test(normalized),
    /[{}[\]]/.test(normalized) && /[:=]/.test(normalized),
  ].filter(Boolean).length;

  const punctuationDensity =
    (normalized.match(/["'{}[\]:=]/g)?.length ?? 0) / Math.max(normalized.length, 1);

  return structuredPatternHits >= 2 || punctuationDensity >= 0.12;
}

function extractLatestThinkingFragment(line: string) {
  const normalized = line.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }

  const fragments = normalized
    .split(/[。！？；：|,，]/)
    .map((fragment) => fragment.trim())
    .filter((fragment) => fragment.length > 0);

  if (fragments.length === 0) {
    return normalized;
  }

  const latest = fragments[fragments.length - 1] ?? normalized;
  const previous = fragments.length > 1 ? fragments[fragments.length - 2] ?? "" : "";

  if (!isLowSignalThinkingFragment(latest) && latest.length >= 6) {
    return latest;
  }

  if (previous && !isLowSignalThinkingFragment(previous) && previous.length > latest.length) {
    return previous;
  }

  if (!isLowSignalThinkingFragment(latest)) {
    return latest;
  }

  return normalized;
}

function clampThinkingTailPreview(text: string, maxChars = 80) {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  if (normalized.length <= maxChars) {
    return normalized;
  }
  return `...${normalized.slice(-maxChars)}`;
}

function buildStreamingThinkingPreview(contentOrLine: string) {
  const latestLine = extractLatestThinkingSummary(contentOrLine);
  if (!latestLine) {
    return "";
  }
  if (isStructuredThinkingLine(latestLine)) {
    return clampThinkingTailPreview(latestLine);
  }
  const fragment = extractLatestThinkingFragment(latestLine);
  return clampThinkingTailPreview(fragment || latestLine);
}

function buildDiagnosisStartContextStreamText({
  alertName,
  severity,
  rootCount,
  affectedCount,
  topologySummary,
}: {
  alertName: string;
  severity: string;
  rootCount: number;
  affectedCount: number;
  topologySummary: string;
}) {
  const fragments = [
    alertName ? `正在聚合 ${alertName} 的告警上下文` : "正在聚合告警上下文",
    severity ? `严重级别 ${severity}` : "",
    rootCount > 0 ? `关联根节点 ${rootCount} 个` : "",
    affectedCount > 0 ? `影响实体 ${affectedCount} 个` : "",
    topologySummary ? `拓扑摘要 ${topologySummary}` : "",
  ].filter((item) => item.length > 0);
  return fragments.join("，");
}

function buildThinkingIdentitySet(item: Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }>) {
  const identitySet = new Set<string>();
  if (item.roundId && item.roundId.trim()) {
    identitySet.add(`round:${item.roundId.trim()}`);
  }
  if (item.thoughtKey && item.thoughtKey.trim()) {
    identitySet.add(`thought:${item.thoughtKey.trim()}`);
  }
  return identitySet;
}

function isSameThinkingRound(
  first: Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }>,
  second: Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }>,
) {
  const firstIdentities = buildThinkingIdentitySet(first);
  if (firstIdentities.size === 0) {
    return false;
  }
  const secondIdentities = buildThinkingIdentitySet(second);
  for (const key of secondIdentities) {
    if (firstIdentities.has(key)) {
      return true;
    }
  }
  return false;
}

function findCompletedThinkingReplacement(
  sourceTimeline: DiagnosisModifiedTimelineItem[],
  streamingItem: Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }>,
) {
  return sourceTimeline.find(
    (
      item,
    ): item is Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }> =>
      item.kind === "thinking" &&
      item.status === "completed" &&
      isSameThinkingRound(item, streamingItem),
  );
}

function clampThinkingPreview(text: string) {
  const normalized = text.trim();
  if (normalized.length <= THINKING_PREVIEW_CHAR_LIMIT) {
    return normalized;
  }
  return `${normalized.slice(0, THINKING_PREVIEW_CHAR_LIMIT)}...`;
}


function resolveInlineError(error?: string): { tone: "error" | "warning"; message: string } | null {
  if (!error) {
    return null;
  }
  if (/status\s+404/i.test(error)) {
    return {
      tone: "warning",
      message:
        "The session could not be found (404). It may have expired or the backend detail endpoint is unavailable.",
    };
  }
  return { tone: "error", message: error };
}

function useProgressiveText(
  text: string,
  animate: boolean,
  speedMs = DEMO_TEXT_SPEED_MS,
  onComplete?: () => void,
) {
  const [displayedText, setDisplayedText] = useState(animate ? "" : text);
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);


  useEffect(() => {
    if (!animate) {
      setDisplayedText(text);
      if (text.length === 0) {
        onCompleteRef.current?.();
      }
      return;
    }

    if (text.length === 0) {
      setDisplayedText("");
      onCompleteRef.current?.();
      return;
    }

    setDisplayedText("");
    let index = 0;
    let completed = false;
    const timer = window.setInterval(() => {
      index += 1;
      setDisplayedText(text.slice(0, index));
      if (index >= text.length) {
        window.clearInterval(timer);
        if (!completed) {
          completed = true;
          onCompleteRef.current?.();
        }
      }
    }, speedMs);

    return () => window.clearInterval(timer);
  }, [animate, speedMs, text]);

  return displayedText;
}

function ToneBadge({ children, tone = "neutral" }: { children: ReactNode; tone?: BadgeTone }) {
  return <span className={cn("diagnosis-modified-badge", `diagnosis-modified-badge--${tone}`)}>{children}</span>;
}

type TraceStepKind = "thought" | "tool_call" | "observation" | "decision" | "status_sync" | "action_generated" | "context";

const TRACE_STEP_META: Record<TraceStepKind, { label: string; glyph: string }> = {
  thought: { label: "Thought / 推理", glyph: "T" },
  tool_call: { label: "Tool Call / 工具调用", glyph: "C" },
  observation: { label: "Observation / 观察结果", glyph: "O" },
  decision: { label: "Decision / 形成判断", glyph: "D" },
  status_sync: { label: "Status Sync / 状态同步", glyph: "S" },
  action_generated: { label: "Action Generated / 生成修复动作", glyph: "A" },
  context: { label: "Context / 上下文", glyph: "I" },
};

function getTraceSyncStageLabel(stageId: string) {
  switch (stageId) {
    case "context":
      return "影响拓扑";
    case "hypotheses":
      return "候选假设";
    case "verification":
      return "继续验证";
    case "confidence":
      return "置信度更新";
    case "remediation":
      return "修复方案";
    default:
      return "诊断中";
  }
}

function TraceStepFrame({
  type,
  title,
  hideTitle = false,
  titleClassName,
  meta,
  summary,
  children,
  className,
  testId,
}: {
  type: TraceStepKind;
  title: string;
  hideTitle?: boolean;
  titleClassName?: string;
  meta?: string;
  summary?: string;
  children?: ReactNode;
  className?: string;
  testId?: string;
}) {
  const typeMeta = TRACE_STEP_META[type];
  const shouldRenderHeader = !hideTitle || Boolean(meta);
  const isMetaOnlyHeader = hideTitle && Boolean(meta);

  return (
    <li
      className={cn("diagnosis-modified-trace-step", `diagnosis-modified-trace-step--${type}`, className)}
      data-testid={testId}
    >
      <div className="diagnosis-modified-trace-step__rail" aria-hidden="true">
        <span>{typeMeta.glyph}</span>
      </div>
      <article className="diagnosis-modified-trace-step__body">
        {shouldRenderHeader ? (
          <header
            className={cn(
              "diagnosis-modified-trace-step__header",
              isMetaOnlyHeader && "diagnosis-modified-trace-step__header--meta-only",
            )}
          >
            {!hideTitle ? (
              <div>
                <h3 className={cn("diagnosis-modified-trace-step__title", titleClassName)}>{title}</h3>
              </div>
            ) : null}
            {meta ? <span className="diagnosis-modified-trace-step__meta">{meta}</span> : null}
          </header>
        ) : null}
        {summary ? <p className="diagnosis-modified-trace-step__summary">{summary}</p> : null}
        {children ? <div className="diagnosis-modified-trace-step__details">{children}</div> : null}
      </article>
    </li>
  );
}

function StreamingText({
  text,
  animate = false,
  className,
  onComplete,
}: {
  text: string;
  animate?: boolean;
  className?: string;
  onComplete?: () => void;
}) {
  const displayedText = useProgressiveText(text, animate, DEMO_TEXT_SPEED_MS, onComplete);
  const showCaret = animate && displayedText.length < text.length;

  return (
    <span className={className}>
      {displayedText}
      {showCaret ? <span className="diagnosis-modified-caret" aria-hidden="true" /> : null}
    </span>
  );
}

function MessageRow({
  item,
  animate,
  onStreamComplete,
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "message" }>;
  animate: boolean;
  onStreamComplete?: () => void;
}) {
  const isUser = item.role === "user";
  const hoverTime = formatTimestamp(item.timestamp);

  return (
    <TraceStepFrame
      meta={hoverTime}
      title={item.label ?? (isUser ? "补充输入" : "形成阶段判断")}
      type={isUser ? "observation" : "decision"}
    >
      <div className={cn("diagnosis-modified-message-row", isUser && "diagnosis-modified-message-row--user")}>
        <div className="diagnosis-modified-message-row__body">
          <span className="diagnosis-modified-message-row__hover-time" aria-hidden="true">
            {hoverTime}
          </span>
        <p className="diagnosis-modified-message-row__text">
          <StreamingText
            animate={animate && !isUser}
            onComplete={animate && !isUser ? onStreamComplete : undefined}
            text={item.content}
          />
        </p>
      </div>
      </div>
    </TraceStepFrame>
  );
}

function StatusSyncRow({
  item,
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "status_sync" }>;
}) {
  const hoverTime = formatTimestamp(item.timestamp);

  return (
    <TraceStepFrame
      meta={hoverTime}
      title={item.title}
      type="status_sync"
    >
      <div className="diagnosis-modified-message-row diagnosis-modified-message-row--status-sync">
        <div className="diagnosis-modified-message-row__body">
          <span className="diagnosis-modified-message-row__hover-time" aria-hidden="true">
            {hoverTime}
          </span>
          <p className="diagnosis-modified-message-row__text">{item.content}</p>
        </div>
      </div>
    </TraceStepFrame>
  );
}

function ContextStartRow({
  item,
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "context_start" }>;
}) {
  const hoverTime = formatTimestamp(item.timestamp);
  return (
    <TraceStepFrame meta={hoverTime} title={item.title} type="context">
      <div className="diagnosis-modified-context-row">
        <div className="diagnosis-modified-context-row__body">
          <span className="diagnosis-modified-message-row__hover-time" aria-hidden="true">
            {hoverTime}
          </span>
          <p className="diagnosis-modified-message-row__text">{item.summary}</p>
          {item.details.length > 0 ? (
            <ul className="diagnosis-modified-tool-card__results">
              {item.details.map((detail) => (
                <li key={detail}>{detail}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </TraceStepFrame>
  );
}

function ThinkingBlock({
  item,
  animate,
  onStreamComplete,
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }>;
  animate: boolean;
  onStreamComplete?: () => void;
}) {
  const isThinking = item.status === "thinking";
  const [isExpanded, setIsExpanded] = useState(true);
  const latestSummaryLine = item.summaryLine?.trim() || extractLatestThinkingSummary(item.content);
  const streamingPreviewText = buildStreamingThinkingPreview(latestSummaryLine || item.content);
  const hasCompletedContent = !isThinking && item.content.trim().length > 0;
  const completedPreviewText = clampThinkingPreview(latestSummaryLine || item.content);

  useEffect(() => {
    if (isThinking) {
      setIsExpanded(true);
      return;
    }
    setIsExpanded(true);
  }, [isThinking, item.content, item.id]);

  const durationLabel = formatThoughtDurationLabel(item.thoughtDurationSec);

  if (isThinking) {
    return (
      <TraceStepFrame meta="Thinking..." title={item.streamingTitle ?? "推理中"} type="thought">
        <div className="diagnosis-modified-process-row">
          <div className="diagnosis-modified-process-row__body">
            <div className="diagnosis-modified-thinking__stream-body">
              <span className="diagnosis-modified-thinking__stream-rail-icon" aria-hidden="true">
                <span className="diagnosis-modified-thinking__pulse" />
              </span>
              <StreamingText
                animate={animate}
                onComplete={onStreamComplete}
                text={streamingPreviewText}
                className="diagnosis-modified-thinking__stream-text diagnosis-modified-thinking__stream-text--single-line"
              />
            </div>
          </div>
        </div>
      </TraceStepFrame>
    );
  }

  if (!hasCompletedContent) {
    return (
      <TraceStepFrame
        meta={durationLabel}
        title="推理完成"
        type="thought"
      >
        <div className="diagnosis-modified-process-row">
          <div className="diagnosis-modified-process-row__body">
            <div className="diagnosis-modified-thinking__content">
              {item.toolName ? <p className="diagnosis-modified-thinking__tool">{item.toolName}</p> : null}
              <p>
                <StreamingText text={item.content} />
              </p>
            </div>
          </div>
        </div>
      </TraceStepFrame>
    );
  }

  return (
    <TraceStepFrame
      meta={durationLabel}
      title="推理完成"
      type="thought"
    >
      <div className="diagnosis-modified-process-row">
        <div className="diagnosis-modified-process-row__body">
          <>
        <button
          className={cn("diagnosis-modified-thinking__toggle", "diagnosis-modified-thinking__toggle--interactive")}
          onClick={() => {
            setIsExpanded((current) => !current);
          }}
          type="button"
        >
          <span className="diagnosis-modified-thinking__icon" aria-hidden="true">
            <span className={cn("diagnosis-modified-thinking__chevron", isExpanded && "diagnosis-modified-thinking__chevron--expanded")} />
          </span>
          <span className="diagnosis-modified-thinking__label-wrap">
            <span className="diagnosis-modified-thinking__label">
              {isExpanded ? "收起推理" : "展开全部推理"}
            </span>
          </span>
          {item.toolName ? <span className="diagnosis-modified-thinking__tool">{item.toolName}</span> : null}
        </button>
        {!isExpanded ? (
          <p className="diagnosis-modified-thinking__summary-line">{completedPreviewText}</p>
        ) : null}

        {isExpanded ? (
          <div className="diagnosis-modified-thinking__panel">
            <div className="diagnosis-modified-thinking__content">
              <p>
                <StreamingText text={item.content} />
              </p>
            </div>
          </div>
        ) : null}
          </>
      </div>
      </div>
    </TraceStepFrame>
  );
}

function ToolCard({ item }: { item: Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> }) {
  const [isExpanded, setIsExpanded] = useState(item.status === "loading");
  const hasDetails = Object.keys(item.params).length > 0 || item.summaryLines.length > 0;

  useEffect(() => {
    if (item.status === "loading") {
      setIsExpanded(true);
      return;
    }

    setIsExpanded(false);
  }, [item.id, item.status]);

  return (
    <TraceStepFrame
      title="执行工具调用"
      type="tool_call"
    >
      <div className="diagnosis-modified-process-row diagnosis-modified-process-row--tool">
        <div className="diagnosis-modified-process-row__body">
        <button
          className={cn(
            "diagnosis-modified-tool-card",
            `diagnosis-modified-tool-card--${item.status}`,
            hasDetails && item.status !== "loading" && "diagnosis-modified-tool-card--interactive",
          )}
          onClick={() => {
            if (hasDetails && item.status !== "loading") {
              setIsExpanded((current) => !current);
            }
          }}
          type="button"
        >
          <div className="diagnosis-modified-tool-card__header">
            <div className="diagnosis-modified-tool-card__status-icon" aria-hidden="true">
              <span className={cn("diagnosis-modified-tool-card__status-indicator", `diagnosis-modified-tool-card__status-indicator--${item.status}`)} />
            </div>
            <div className="diagnosis-modified-tool-card__header-copy">
              <strong>{item.toolName}</strong>
              {Object.keys(item.params).length > 0 ? <span>{JSON.stringify(item.params)}</span> : null}
            </div>
            <span className="diagnosis-modified-tool-card__status-label">{item.status}</span>
          </div>

          {isExpanded ? (
            <div className="diagnosis-modified-tool-card__body">
              {item.status === "loading" ? (
                <div className="diagnosis-modified-tool-card__loading">
                  <div>
                    <span className="diagnosis-modified-tool-card__loading-indicator" />
                    <span>Connecting to telemetry stream...</span>
                  </div>
                  <div>
                    <span className="diagnosis-modified-tool-card__loading-indicator" />
                    <span>Querying diagnostics context...</span>
                  </div>
                </div>
              ) : (
                <div className="diagnosis-modified-tool-card__details">
                  {Object.keys(item.params).length > 0 ? (
                    <pre className="diagnosis-modified-tool-card__params">{JSON.stringify(item.params, null, 2)}</pre>
                  ) : null}
                  {item.summaryLines.length > 0 ? (
                    <div className="diagnosis-modified-tool-card__results">
                      {item.summaryLines.map((line) => (
                        <p key={line}>{line}</p>
                      ))}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          ) : null}
        </button>
      </div>
      </div>
    </TraceStepFrame>
  );
}

function getActionGeneratedSummary(status?: string) {
  switch (String(status ?? "").trim().toLowerCase()) {
    case "approval_required":
    case "awaiting_approval":
      return "当前进入待审批阶段。";
    case "approved":
      return "修复建议已批准，等待执行链路推进。";
    case "remediating":
      return "修复动作正在执行。";
    case "validating":
      return "修复动作已进入验证阶段。";
    case "resolved":
    case "closed":
      return "修复链路已完成收口。";
    case "failed":
    case "timeout":
    case "escalated":
      return "修复链路需要继续跟进。";
    default:
      return "已生成修复建议。";
  }
}

function ActionGeneratedStep({
  children,
  plan,
  status,
}: {
  children: ReactNode;
  plan?: DiagnosisModifiedPlanView;
  status?: string;
}) {
  return (
    <TraceStepFrame
      className="diagnosis-modified-trace-step--remediation"
      summary={getActionGeneratedSummary(status)}
      testId="diagnosis-modified-action-generated-step"
      title="已生成修复建议"
      type="action_generated"
    >
      {children}
    </TraceStepFrame>
  );
}

function RCAReportCard({
  summary,
  candidates,
  hypotheses,
  propagationChain,
  timeline,
}: {
  summary: DiagnosisModifiedSummaryView;
  candidates: DiagnosisModifiedCandidateView[];
  hypotheses?: DiagnosisModifiedHypothesisView[];
  propagationChain?: DiagnosisModifiedPropagationStepView[];
  timeline: DiagnosisModifiedTimelineItem[];
}) {
  const primaryCandidate = candidates[0];
  const lastTimestamp = timeline[timeline.length - 1]?.timestamp;
  const lastParts = formatDateTimeParts(lastTimestamp);
  const hypothesisRows = hypotheses ?? [];
  const chainRows = propagationChain ?? [];

  return (
    <section className="diagnosis-modified-report-card">
      <header className="diagnosis-modified-report-card__header">
        <div>
          <div className="diagnosis-modified-report-card__eyebrow">
            <ToneBadge tone="neutral">\u6839\u56e0\u8bca\u65ad</ToneBadge>
            {summary.priorityLabel ? <ToneBadge tone="warning">{summary.priorityLabel}</ToneBadge> : null}
            <ToneBadge tone={summary.certaintyTone}>{summary.certaintyLabel}</ToneBadge>
          </div>
          <h3 className="diagnosis-modified-report-card__title">{summary.title}</h3>
        </div>
        <div className="diagnosis-modified-report-card__meta">
          {summary.sessionLabel ? <span>{summary.sessionLabel}</span> : null}
          {lastTimestamp ? <span>{`${lastParts.date} ${lastParts.time}`}</span> : null}
        </div>
      </header>

      <div className="diagnosis-modified-report-card__grid">
        <div>
          <span>\u786e\u5b9a\u6027</span>
          <strong>{summary.certaintyLabel}</strong>
        </div>
        <div>
          <span>\u7f6e\u4fe1\u5ea6</span>
          <strong>{summary.confidenceRawLabel ?? summary.confidenceLabel}</strong>
        </div>
        <div>
          <span>\u4f18\u5148\u7ea7</span>
          <strong>{summary.priorityLabel ?? "--"}</strong>
        </div>
        <div>
          <span>\u66f4\u65b0\u65f6\u95f4</span>
          <strong>{lastTimestamp ? lastParts.time : "--"}</strong>
        </div>
      </div>

      <div className="diagnosis-modified-report-card__sections">
        <div>
          <p className="diagnosis-modified-report-card__section-label">\u5f53\u524d\u7ed3\u8bba</p>
          <div className="diagnosis-modified-report-card__facts">
            <p>
              <span>\u6839\u56e0\uff1a</span>
              {summary.rootCause ?? primaryCandidate?.title ?? "--"}
            </p>
            <p>
              <span>\u5c42\u7ea7\uff1a</span>
              {summary.rootCauseLayerLabel ?? summary.rootCauseLayer ?? primaryCandidate?.layer ?? "--"}
            </p>
            <p>
              <span>\u5b9e\u4f53\uff1a</span>
              {summary.rootCauseEntities && summary.rootCauseEntities.length > 0
                ? summary.rootCauseEntities.join("\uff0c")
                : primaryCandidate?.entities?.join("\uff0c") || "--"}
            </p>
            <p>
              <span>\u5f71\u54cd\uff1a</span>
              {summary.impactSummary}
            </p>
            <p>
              <span>\u53d7\u5f71\u54cd\u670d\u52a1\uff1a</span>
              {summary.affectedServices.length > 0 ? summary.affectedServices.join("\uff0c") : "--"}
            </p>
          </div>
        </div>

        <div>
          <p className="diagnosis-modified-report-card__section-label">\u5019\u9009\u6839\u56e0\uff08{candidates.length}\uff09</p>
          <div className="diagnosis-modified-report-card__candidates">
            {candidates.length > 0 ? (
              candidates.map((candidate, index) => (
                <div key={candidate.id} className="diagnosis-modified-report-card__candidate-item">
                  <p>
                    #{candidate.rank ?? index + 1} {candidate.title} ({candidate.confidence.toFixed(2)}) \u8bc1\u636e\u6458\u8981\uff1a
                    {candidate.evidenceSummary ?? candidate.summary}
                  </p>
                  {candidate.distinguishingVerification ? <p>\u533a\u5206\u9a8c\u8bc1\uff1a{candidate.distinguishingVerification}</p> : null}
                </div>
              ))
            ) : (
              <p className="diagnosis-modified-report-card__section-copy">\u6682\u65e0\u5019\u9009\u6839\u56e0\u3002</p>
            )}
          </div>
        </div>

        <div>
          <p className="diagnosis-modified-report-card__section-label">\u5047\u8bbe\u4e0e\u8bc1\u636e</p>
          <div className="diagnosis-modified-report-card__hypotheses">
            {hypothesisRows.length > 0 ? (
              hypothesisRows.map((item, index) => (
                <p key={item.id}>
                  {String.fromCharCode(65 + index)}. {item.description} [{item.statusLabel}] \u652f\u6301\u8bc1\u636e({item.evidenceForCount}) \u53cd\u8bc1({item.evidenceAgainstCount})
                </p>
              ))
            ) : (
              <p className="diagnosis-modified-report-card__section-copy">\u6682\u65e0\u5047\u8bbe\u8bc1\u636e\u6570\u636e\u3002</p>
            )}
          </div>
        </div>

        <div>
          <details className="diagnosis-modified-report-card__propagation">
            <summary className="diagnosis-modified-report-card__section-label">\u4f20\u64ad\u94fe\u8def\uff08\u6298\u53e0\uff09</summary>
            <div className="diagnosis-modified-report-card__propagation-body">
              {chainRows.length > 0 ? (
                chainRows.map((step) => (
                  <p key={step.id}>
                    {step.entityId} -&gt; {step.metric} -&gt; {step.valueBefore} -&gt; {step.valueAfter} -&gt; {step.description}
                  </p>
                ))
              ) : (
                <p className="diagnosis-modified-report-card__section-copy">\u6682\u65e0\u4f20\u64ad\u94fe\u8def\u6570\u636e\u3002</p>
              )}
            </div>
          </details>
        </div>
      </div>
    </section>
  );
}
function getApprovalConfidenceMeta(label?: string) {
  if (!label) {
    return null;
  }

  return /\u7f6e\u4fe1/u.test(label) ? label : `${label} \u7f6e\u4fe1`;
}

function ApprovalPlanCard({
  plan,
  canApprove,
  approvalBlockReason,
  isSubmitting,
  resolution,
  onApprove,
}: {
  plan: DiagnosisModifiedPlanView;
  canApprove: boolean;
  approvalBlockReason?: string;
  isSubmitting: boolean;
  resolution: ApprovalPlanResolution | null;
  onApprove: () => void;
}) {
  const statusTone = resolution?.tone ?? (canApprove ? "accent" : "neutral");
  const statusLabel = resolution?.label ?? (canApprove ? "\u5f85\u5ba1\u6279" : "\u5f85\u5904\u7406");
  const metaItems = [
    plan.priorityLabel,
    getApprovalConfidenceMeta(plan.confidenceLabel) ?? undefined,
    plan.safetyLabel,
    plan.canaryLabel,
  ].filter((item): item is string => Boolean(item && item.trim().length > 0));

  return (
    <section
      className={cn(
        "diagnosis-modified-approval-card",
        resolution?.tone === "success" && "diagnosis-modified-approval-card--approved",
        resolution?.tone === "danger" && "diagnosis-modified-approval-card--rejected",
      )}
      data-testid="diagnosis-modified-approval-surface"
    >
      <header className="diagnosis-modified-approval-card__header">
        <div>
          <div className="diagnosis-modified-approval-card__eyebrow">
            <ToneBadge tone="neutral">\u8bca\u65ad\u4fee\u590d\u65b9\u6848</ToneBadge>
            <ToneBadge tone={statusTone}>{statusLabel}</ToneBadge>
          </div>
          <h3 className="diagnosis-modified-approval-card__title">{plan.title}</h3>
          <p className="diagnosis-modified-approval-card__description">{plan.description}</p>
        </div>
      </header>

      <div className="diagnosis-modified-approval-card__body">
        {metaItems.length > 0 ? (
          <div className="diagnosis-modified-approval-card__meta-row">
            {metaItems.map((item) => (
              <ToneBadge key={item} tone="neutral">
                {item}
              </ToneBadge>
            ))}
          </div>
        ) : null}

        {plan.impactSummary ? (
          <section className="diagnosis-workspace-approval-surface__impact">
            <span className="diagnosis-workspace-approval-surface__section-label">\u98ce\u9669\u5f71\u54cd</span>
            <p>{plan.impactSummary}</p>
          </section>
        ) : null}

        <div className="diagnosis-modified-approval-card__actions-wrap">
          <ol className="diagnosis-modified-approval-card__actions">
            {plan.steps.map((step, index) => (
              <li key={step.id}>
                <span>{index + 1}</span>
                <div>
                  <strong>{step.title}</strong>
                  {step.detail ? <p>{step.detail}</p> : null}
                  {step.paramsSummary ? <code>{step.paramsSummary}</code> : null}
                </div>
              </li>
            ))}
          </ol>
        </div>

        {!resolution && approvalBlockReason ? (
          <p className="diagnosis-workspace-approval-surface__note diagnosis-workspace-approval-surface__note--warning">
            {approvalBlockReason}
          </p>
        ) : null}

        {isSubmitting ? (
          <div className="diagnosis-modified-approval-card__updating">\u6b63\u5728\u63d0\u4ea4\u5ba1\u6279...</div>
        ) : null}

        {resolution ? (
          <div className="diagnosis-modified-approval-card__resolved">
            <ToneBadge tone={resolution.tone}>{resolution.label}</ToneBadge>
            <p>{resolution.description}</p>
          </div>
        ) : (
          <div className="diagnosis-modified-approval-card__footer">
            <button
              className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary"
              disabled={isSubmitting || !canApprove}
              onClick={onApprove}
              type="button"
            >
              {isSubmitting ? "\u6b63\u5728\u63d0\u4ea4..." : "\u540c\u610f\u6267\u884c"}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

function DiagnosisModifiedPage() {
  const params = useParams<{ sessionId?: string }>();
  const routeSessionId = (params.sessionId ?? "").trim();
  const shouldBootstrapLiveSession = routeSessionId.length > 0;
  const [demoTimeline, setDemoTimeline] = useState<DiagnosisModifiedTimelineItem[]>([]);
  const [demoCandidates, setDemoCandidates] = useState<DiagnosisModifiedCandidateView[]>([]);
  const [demoCandidateSnapshots, setDemoCandidateSnapshots] = useState<
    Array<{ id: string; timestamp: string; candidates: DiagnosisModifiedCandidateView[] }>
  >([]);
  const [demoSummary, setDemoSummary] = useState<DiagnosisModifiedSummaryView | undefined>();
  const [demoPlan, setDemoPlan] = useState<DiagnosisModifiedPlanView | undefined>();
  const [demoSession, setDemoSession] = useState<DiagnosisSession | undefined>();
  const [demoEvents, setDemoEvents] = useState<SessionEvent[]>([]);
  const [demoLocalAuditRecords, setDemoLocalAuditRecords] = useState<DiagnosisLocalAuditRecord[]>([]);
  const [demoHypotheses, setDemoHypotheses] = useState<DiagnosisModifiedHypothesisView[]>([]);
  const [demoPropagationChain, setDemoPropagationChain] = useState<DiagnosisModifiedPropagationStepView[]>([]);
  const [lastDemoPrompt, setLastDemoPrompt] = useState("");
  const [activeStreamingMessageId, setActiveStreamingMessageId] = useState<string | null>(null);
  const [liveTimeline, setLiveTimeline] = useState<DiagnosisModifiedTimelineItem[]>([]);
  const [demoApprovalState, setDemoApprovalState] = useState<ApprovalState>("pending");
  const [demoActionFeedback, setDemoActionFeedback] = useState<string | null>(null);
  const [liveApprovalResolution, setLiveApprovalResolution] = useState<ApprovalPlanResolution | null>(null);

  const timelineScrollRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const timelineAutoFollowRef = useRef(true);
  const forceTimelineAutoFollowRef = useRef(true);
  const timelineSessionKeyRef = useRef<string | null>(null);
  const demoTimerRef = useRef<number[]>([]);
  const demoRunTokenRef = useRef(0);

  const messageStreamResolversRef = useRef<Map<string, () => void>>(new Map());
  const messageStreamFallbackTimersRef = useRef<Map<string, number>>(new Map());

  const thinkingStreamResolversRef = useRef<Map<string, () => void>>(new Map());
  const thinkingStreamFallbackTimersRef = useRef<Map<string, number>>(new Map());
  const demoThinkingStartedAtRef = useRef<Map<string, number>>(new Map());
  const demoToolLoadingStartedAtRef = useRef<Map<string, number>>(new Map());

  const liveToolWaiterResolversRef = useRef<Map<string, () => void>>(new Map());
  const liveToolTimeoutTimersRef = useRef<Map<string, number>>(new Map());

  const liveTimelineRef = useRef<DiagnosisModifiedTimelineItem[]>([]);
  const liveQueuedItemsRef = useRef<DiagnosisModifiedTimelineItem[]>([]);
  const liveQueuedIdsRef = useRef<Set<string>>(new Set());
  const liveDisplayedIdsRef = useRef<Set<string>>(new Set());
  const liveQueueProcessingRef = useRef(false);
  const liveQueueTokenRef = useRef(0);
  const latestLiveSourceByIdRef = useRef<Map<string, DiagnosisModifiedTimelineItem>>(new Map());
  const initializedLiveSessionIdRef = useRef<string | null>(null);

  const {
    session,
    activeSessionId,
    messages,
    events,
    localAuditRecords,
    alertSnapshot,
    topologyContext,
    liveThinking,
    liveFinalAnswer,
    activeStreamingTools,
    bootstrapStatus,
    error,
    isApprovingPlan,
    canApprove,
    approvalBlockReason,
    currentPlanVersion,
    approvePlan,
    bootstrapSession,
    reconcileSession,
    applyEvent,
    setConnectionState,
  } = useDiagnosisStore();

  const liveView = useMemo(() => buildDiagnosisModifiedLiveView(session, messages, events), [events, messages, session]);
  const isTerminalLiveSession = TERMINAL_SESSION_STATUSES.has(
    String(session?.status ?? "").trim().toLowerCase(),
  );
  const hasCompletedContextBootstrap = events.some((event) => event.type === "node_completed");
  const hasActiveThinkingStream = Boolean(
    liveThinking &&
    liveThinking.status === "thinking" &&
    liveThinking.content.trim().length > 0 &&
    (liveThinking.node ?? "").trim().toLowerCase() !== "bootstrap",
  );
  const activeStreamingThinkingItem = useMemo<Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }> | null>(() => {
    if (!hasActiveThinkingStream || !liveThinking) {
      return null;
    }
    return {
      id: liveThinking.round_id ?? `live-thinking-${liveThinking.thought_key}`,
      kind: "thinking",
      title: "Agent is analyzing the request",
      content: normalizeDiagnosisModifiedDisplayText(liveThinking.content),
      timestamp: liveThinking.timestamp,
      toolName: liveThinking.tool_name ?? undefined,
      status: "thinking",
      thoughtKey: liveThinking.thought_key,
      roundId: liveThinking.round_id ?? null,
      phase: "streaming",
      summaryLine: extractLatestThinkingSummary(liveThinking.content),
    };
  }, [hasActiveThinkingStream, liveThinking]);
  const activeSessionSourceId = activeSessionId ?? session?.session_id ?? "current";
  const liveContextThinkingItem = useMemo<Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }> | null>(() => {
    if (!shouldBootstrapLiveSession || isTerminalLiveSession) {
      return null;
    }
    if (!alertSnapshot && !topologyContext) {
      return null;
    }
    const normalizedAlertName = normalizeDiagnosisModifiedDisplayText(alertSnapshot?.alert_name ?? "");
    const severity = normalizeDiagnosisModifiedDisplayText(alertSnapshot?.severity ?? "");
    const topologySummary = normalizeDiagnosisModifiedDisplayText(topologyContext?.summary ?? "");
    const rootCount = Array.isArray(topologyContext?.roots) ? topologyContext.roots.length : 0;
    const affectedCount = Number(topologyContext?.affected_count ?? 0);
    const contextText = buildDiagnosisStartContextStreamText({
      alertName: normalizedAlertName,
      severity,
      rootCount,
      affectedCount,
      topologySummary,
    });
    if (!contextText) {
      return null;
    }

    const contextId = `${LIVE_CONTEXT_THINKING_ID_PREFIX}${activeSessionSourceId}`;
    const contextRoundId = `${contextId}:round`;
    return {
      id: contextId,
      kind: "thinking",
      title: "推理完成",
      streamingTitle: "正在构建上下文...",
      content: contextText,
      timestamp: session?.alert.starts_at ?? new Date().toISOString(),
      status: hasCompletedContextBootstrap ? "completed" : "thinking",
      thoughtKey: `${contextId}:thought`,
      roundId: contextRoundId,
      phase: hasCompletedContextBootstrap ? "completed" : "streaming",
      summaryLine: extractLatestThinkingSummary(contextText),
    };
  }, [
    activeSessionSourceId,
    alertSnapshot?.alert_name,
    alertSnapshot?.severity,
    hasCompletedContextBootstrap,
    isTerminalLiveSession,
    session?.alert.alert_name,
    session?.alert.severity,
    session?.alert.starts_at,
    shouldBootstrapLiveSession,
    topologyContext?.affected_count,
    topologyContext?.roots,
    topologyContext?.summary,
  ]);
  const liveTimelineSource = useMemo<DiagnosisModifiedTimelineItem[]>(() => {
    let timeline = [...liveView.timeline];
    const normalizedAlertName = normalizeDiagnosisModifiedDisplayText(alertSnapshot?.alert_name ?? session?.alert.alert_name ?? "");
    const severity = normalizeDiagnosisModifiedDisplayText(alertSnapshot?.severity ?? session?.alert.severity ?? "");
    const topologySummary = normalizeDiagnosisModifiedDisplayText(topologyContext?.summary ?? "");
    const rootCount = Array.isArray(topologyContext?.roots) ? topologyContext.roots.length : 0;
    const affectedCount = Number(topologyContext?.affected_count ?? 0);
    const contextDetails: string[] = [];
    if (severity) {
      contextDetails.push(`严重级别: ${severity}`);
    }
    if (rootCount > 0) {
      contextDetails.push(`关联根节点: ${rootCount}`);
    }
    if (affectedCount > 0) {
      contextDetails.push(`影响实体: ${affectedCount}`);
    }
    if (topologySummary) {
      contextDetails.push(`拓扑摘要: ${topologySummary}`);
    }
    if (liveContextThinkingItem) {
      timeline = timeline.filter((item) => {
        if (item.kind === "context_start") {
          return false;
        }
        if (item.kind !== "thinking") {
          return true;
        }
        if (item.id === liveContextThinkingItem.id) {
          return false;
        }
        if (!hasCompletedContextBootstrap && item.status === "thinking" && item.phase === "streaming") {
          return false;
        }
        return true;
      });
      timeline.unshift(liveContextThinkingItem);
    } else if (
      (isTerminalLiveSession || !shouldBootstrapLiveSession) &&
      (normalizedAlertName || contextDetails.length > 0) &&
      !timeline.some((item) => item.kind === "context_start")
    ) {
      timeline.unshift({
        id: `live-context-start-${activeSessionSourceId}`,
        kind: "context_start",
        title: "诊断开始上下文",
        summary: normalizedAlertName
          ? `已接收告警 ${normalizedAlertName}，开始构建诊断上下文。`
          : "已接收告警并开始构建诊断上下文。",
        details: contextDetails,
        timestamp: session?.alert.starts_at ?? new Date().toISOString(),
      });
    }

    if (activeStreamingThinkingItem && (!liveContextThinkingItem || liveContextThinkingItem.status === "completed")) {
      timeline = timeline.filter((item) => {
        if (item.kind !== "thinking" || item.status !== "completed") {
          return true;
        }
        return !isSameThinkingRound(item, activeStreamingThinkingItem);
      });
      timeline.push(activeStreamingThinkingItem);
    }

    activeStreamingTools.forEach((toolCall, index) => {
      const toolId = `live-tool-${toolCall.round_id ?? toolCall.thought_key ?? "round"}-${toolCall.tool}-${index}`;
      const alreadyExists = timeline.some((item) => item.kind === "tool" && item.id === toolId);
      if (alreadyExists) {
        return;
      }
      timeline.push({
        id: toolId,
        kind: "tool",
        toolName: toolCall.tool,
        params: toolCall.params,
        timestamp: liveThinking?.timestamp ?? new Date().toISOString(),
        status: "loading",
        summaryLines: ["Waiting for tool result..."],
        rawResult: undefined,
      });
    });

    if (liveFinalAnswer?.content.trim()) {
      timeline.push({
        id: liveFinalAnswer.id,
        kind: "message",
        role: "assistant",
        content: normalizeDiagnosisModifiedDisplayText(liveFinalAnswer.content),
        timestamp: liveFinalAnswer.timestamp,
        label: "修复状态更新",
      });
    }

    return timeline;
  }, [
    activeSessionId,
    activeSessionSourceId,
    activeStreamingTools,
    alertSnapshot?.alert_name,
    alertSnapshot?.severity,
    hasCompletedContextBootstrap,
    isTerminalLiveSession,
    liveFinalAnswer,
    liveContextThinkingItem,
    liveThinking,
    liveView.timeline,
    activeStreamingThinkingItem,
    hasActiveThinkingStream,
    session?.alert.alert_name,
    session?.alert.severity,
    session?.alert.starts_at,
    session?.session_id,
    shouldBootstrapLiveSession,
    topologyContext?.affected_count,
    topologyContext?.roots,
    topologyContext?.summary,
  ]);
  const hasLiveSession =
    shouldBootstrapLiveSession && bootstrapStatus === "ready" && Boolean(session) && Boolean(activeSessionId);

  const handleTimelineScroll = useCallback(() => {
    const container = timelineScrollRef.current;
    if (!container) {
      return;
    }
    timelineAutoFollowRef.current = isTimelineNearBottom(container);
  }, []);

  useEffect(() => {
    const timelineSessionKey = activeSessionId ?? session?.session_id ?? routeSessionId ?? "__demo__";
    if (timelineSessionKeyRef.current === timelineSessionKey) {
      return;
    }
    timelineSessionKeyRef.current = timelineSessionKey;
    timelineAutoFollowRef.current = true;
    forceTimelineAutoFollowRef.current = true;
  }, [activeSessionId, routeSessionId, session?.session_id]);

  useEffect(() => {
    if (!shouldBootstrapLiveSession) {
      return;
    }
    void bootstrapSession(routeSessionId);
  }, [bootstrapSession, routeSessionId, shouldBootstrapLiveSession]);

  useEffect(() => {
    if (!shouldBootstrapLiveSession || bootstrapStatus !== "ready" || !activeSessionId || !session?.session_id) {
      return undefined;
    }
    if (isTerminalLiveSession) {
      return undefined;
    }

    void reconcileSession(activeSessionId);
    const timer = window.setInterval(() => {
      void reconcileSession(activeSessionId);
    }, LIVE_SESSION_RECONCILE_INTERVAL_MS);

    return () => {
      window.clearInterval(timer);
    };
  }, [
    activeSessionId,
    bootstrapStatus,
    isTerminalLiveSession,
    reconcileSession,
    session?.session_id,
    shouldBootstrapLiveSession,
  ]);

  useEffect(() => {
    setLiveApprovalResolution(null);
  }, [currentPlanVersion, session?.session_id]);

  const handleApproveDemoPlan = useCallback(() => {
    if (!demoSession?.session_id) {
      return;
    }

    const sessionId = demoSession.session_id;
    const approvedAt = Date.now();

    setDemoApprovalState("approved");
    setDemoActionFeedback("\u5df2\u5728\u8bca\u65ad\u9875\u5185\u786e\u8ba4\u8be5\u4fee\u590d\u65b9\u6848\uff0c\u7cfb\u7edf\u5c06\u81ea\u52a8\u8fdb\u5165\u540e\u7eed\u6267\u884c\u4e0e\u9a8c\u8bc1\u94fe\u8def\u3002");
    setDemoSession((current) =>
      current
        ? {
            ...current,
            status: "approved",
          }
        : current,
    );

    DEMO_POST_APPROVAL_AUTO_FLOW.forEach(({ delayMs, stage }) => {
      const timerId = window.setTimeout(() => {
        const timestamp = new Date(approvedAt + delayMs).toISOString();

        if (stage === "approval_recorded") {
          setDemoTimeline((current) => [
            ...current,
            createDemoTimelineSyncMessage({
              stage,
              timestamp,
              content: "审批已通过，系统已记录修复执行指令。",
            }),
          ]);
          setDemoLocalAuditRecords((current) => [
            ...current,
            createDemoAuditRecord({
              eventKind: "approval_result",
              sessionId,
              statusTone: "success",
              summary: "[system] approval recorded",
              details: ["Execute 10% canary first, then observe Redis timeout recovery."],
              timestamp,
            }),
          ]);
          return;
        }

        if (stage === "canary_progress") {
          setDemoTimeline((current) => [
            ...current,
            createDemoTimelineSyncMessage({
              stage,
              timestamp,
              content: "修复执行已启动：10% 灰度验证进行中。",
            }),
          ]);
          setDemoSession((current) =>
            current
              ? {
                  ...current,
                  status: "validating",
                }
              : current,
          );
          setDemoActionFeedback("\u5df2\u5b8c\u6210\u5ba1\u6279\uff0c\u6b63\u5728\u6309 10% \u7070\u5ea6\u7b56\u7565\u6267\u884c\u9996\u8f6e\u9a8c\u8bc1\u3002");
          setDemoEvents((current) => [
            ...current,
            createDemoSessionEvent({
              sessionId,
              timestamp,
              type: "remediation_progress",
              data: {
                stage: "canary_progress",
                progress: 10,
                progress_label: "Canary 10% in progress",
                message: "Guarded canary execution has started for the approved plan.",
              },
            }),
          ]);
          return;
        }

        if (stage === "metric_feedback") {
          setDemoTimeline((current) => [
            ...current,
            createDemoTimelineSyncMessage({
              stage,
              timestamp,
              content: "指标反馈：延迟与错误率回落到受控范围。",
            }),
          ]);
          setDemoSession((current) =>
            current
              ? {
                  ...current,
                  status: "validating",
                }
              : current,
          );
          setDemoActionFeedback("\u7070\u5ea6\u5df2\u8fdb\u5165\u6307\u6807\u89c2\u5bdf\u9636\u6bb5\uff0c\u6b63\u5728\u7b49\u5f85\u7a33\u5b9a\u6027\u786e\u8ba4\u3002");
          setDemoEvents((current) => [
            ...current,
            createDemoSessionEvent({
              sessionId,
              timestamp,
              type: "observation_result",
              data: {
                stage: "observation_result",
                metrics_improved: true,
                alert_cleared: false,
                progress_label: "p95 latency and error rate returned to guarded thresholds.",
                message: "Key latency and error metrics are trending back to baseline.",
              },
            }),
          ]);
          return;
        }

        if (stage === "alert_recovery") {
          setDemoTimeline((current) => [
            ...current,
            createDemoTimelineSyncMessage({
              stage,
              timestamp,
              content: "告警恢复：主告警已恢复，准备关闭诊断会话。",
            }),
          ]);
          setDemoSession((current) =>
            current
              ? {
                  ...current,
                  status: "resolved",
                  outcome: "resolved",
                }
              : current,
          );
          setDemoActionFeedback("\u5173\u952e\u544a\u8b66\u5df2\u6062\u590d\uff0c\u7cfb\u7edf\u6b63\u5728\u5b8c\u6210\u6700\u540e\u7684 session \u6536\u53e3\u3002");
          setDemoEvents((current) => [
            ...current,
            createDemoSessionEvent({
              sessionId,
              timestamp,
              type: "remediation_progress",
              data: {
                stage: "alert_recovered",
                message: "Primary alert recovered after the guarded canary.",
              },
            }),
          ]);
          return;
        }

        setDemoTimeline((current) => [
          ...current,
          createDemoTimelineSyncMessage({
            stage,
            timestamp,
            content: "会话关闭：诊断与修复验证已完成收口。",
          }),
        ]);
        setDemoSession((current) =>
          current
            ? {
                ...current,
                status: "closed",
                outcome: "resolved",
              }
            : current,
        );
        setDemoActionFeedback("\u4fee\u590d\u9a8c\u8bc1\u5df2\u5b8c\u6210\uff0csession \u5df2\u5173\u95ed\uff0c\u53ef\u5728\u53f3\u4fa7\u67e5\u770b\u5df2\u538b\u7f29\u7684\u4fee\u590d\u4e8b\u4ef6\u6458\u8981\u3002");
        setDemoEvents((current) => [
          ...current,
          createDemoSessionEvent({
            sessionId,
            timestamp,
            type: "remediation_progress",
            data: {
              stage: "session_closed",
              message: "Session closed after verification.",
            },
          }),
        ]);
      }, delayMs);

      demoTimerRef.current.push(timerId);
    });
  }, [demoSession]);

  const handleApproveLivePlan = useCallback(async () => {
    await approvePlan({ approved: true });
    setLiveApprovalResolution({
      label: "\u5df2\u63d0\u4ea4",
      tone: "success",
      description: "\u4fee\u590d\u65b9\u6848\u5df2\u63d0\u4ea4\u5ba1\u6279\u6307\u4ee4\uff0c\u5373\u5c06\u8fdb\u5165\u6267\u884c\u94fe\u8def\u3002",
    });
  }, [approvePlan]);

  const clearDemoTimers = useCallback(() => {
    demoTimerRef.current.forEach((timerId) => window.clearTimeout(timerId));
    demoTimerRef.current = [];
  }, []);

  const clearPendingMessageStreams = useCallback(() => {
    for (const timerId of messageStreamFallbackTimersRef.current.values()) {
      window.clearTimeout(timerId);
    }
    messageStreamFallbackTimersRef.current.clear();

    for (const resolver of messageStreamResolversRef.current.values()) {
      resolver();
    }
    messageStreamResolversRef.current.clear();
    setActiveStreamingMessageId(null);
  }, []);
  const clearPendingThinkingStreams = useCallback(() => {
    for (const timerId of thinkingStreamFallbackTimersRef.current.values()) {
      window.clearTimeout(timerId);
    }
    thinkingStreamFallbackTimersRef.current.clear();

    for (const resolver of thinkingStreamResolversRef.current.values()) {
      resolver();
    }
    thinkingStreamResolversRef.current.clear();
    demoThinkingStartedAtRef.current.clear();
  }, []);

  const clearDemoToolLoadingStates = useCallback(() => {
    demoToolLoadingStartedAtRef.current.clear();
  }, []);

  const clearLiveToolWaiters = useCallback(() => {
    for (const timeoutId of liveToolTimeoutTimersRef.current.values()) {
      window.clearTimeout(timeoutId);
    }
    liveToolTimeoutTimersRef.current.clear();

    for (const resolver of liveToolWaiterResolversRef.current.values()) {
      resolver();
    }
    liveToolWaiterResolversRef.current.clear();
  }, []);

  useEffect(
    () => () => {
      clearDemoTimers();
      clearPendingMessageStreams();
      clearPendingThinkingStreams();
      clearDemoToolLoadingStates();
      clearLiveToolWaiters();
    },
    [
      clearDemoTimers,
      clearDemoToolLoadingStates,
      clearLiveToolWaiters,
      clearPendingMessageStreams,
      clearPendingThinkingStreams,
    ],
  );

  const resolveMessageStream = useCallback((messageId: string) => {
    const resolver = messageStreamResolversRef.current.get(messageId);
    resolver?.();
  }, []);

  const waitForMessageStream = useCallback((messageId: string, content: string) => {
    if (!content.length) {
      return Promise.resolve();
    }

    return new Promise<void>((resolve) => {
      let settled = false;
      const settle = () => {
        if (settled) {
          return;
        }
        settled = true;

        const fallbackTimerId = messageStreamFallbackTimersRef.current.get(messageId);
        if (fallbackTimerId) {
          window.clearTimeout(fallbackTimerId);
          messageStreamFallbackTimersRef.current.delete(messageId);
        }

        messageStreamResolversRef.current.delete(messageId);
        setActiveStreamingMessageId((current) => (current === messageId ? null : current));
        resolve();
      };

      messageStreamResolversRef.current.set(messageId, settle);
      setActiveStreamingMessageId(messageId);

      const fallbackDelay = Math.max(
        STREAM_COMPLETION_BUFFER_MS,
        content.length * DEMO_TEXT_SPEED_MS + STREAM_COMPLETION_BUFFER_MS,
      );
      const fallbackTimerId = window.setTimeout(settle, fallbackDelay);
      messageStreamFallbackTimersRef.current.set(messageId, fallbackTimerId);
    });
  }, []);

  const resolveThinkingStream = useCallback((thinkingId: string) => {
    const resolver = thinkingStreamResolversRef.current.get(thinkingId);
    resolver?.();
  }, []);

  const waitForThinkingStream = useCallback((thinkingId: string, content: string) => {
    if (!content.length) {
      return Promise.resolve();
    }

    return new Promise<void>((resolve) => {
      let settled = false;
      const settle = () => {
        if (settled) {
          return;
        }
        settled = true;

        const fallbackTimerId = thinkingStreamFallbackTimersRef.current.get(thinkingId);
        if (fallbackTimerId) {
          window.clearTimeout(fallbackTimerId);
          thinkingStreamFallbackTimersRef.current.delete(thinkingId);
        }

        thinkingStreamResolversRef.current.delete(thinkingId);
        resolve();
      };

      thinkingStreamResolversRef.current.set(thinkingId, settle);
      const fallbackDelay = Math.max(
        STREAM_COMPLETION_BUFFER_MS,
        content.length * DEMO_TEXT_SPEED_MS + STREAM_COMPLETION_BUFFER_MS,
      );
      const fallbackTimerId = window.setTimeout(settle, fallbackDelay);
      thinkingStreamFallbackTimersRef.current.set(thinkingId, fallbackTimerId);
    });
  }, []);

  const resolveLiveToolWaitersIfReady = useCallback((timelineItems: DiagnosisModifiedTimelineItem[]) => {
    for (const [toolId, resolver] of [...liveToolWaiterResolversRef.current.entries()]) {
      const toolItem = timelineItems.find(
        (item): item is Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> =>
          item.kind === "tool" && item.id === toolId,
      );
      if (toolItem && toolItem.status !== "loading") {
        resolver();
      }
    }
  }, []);

  useEffect(() => {
    liveTimelineRef.current = liveTimeline;
    resolveLiveToolWaitersIfReady(liveTimeline);
  }, [liveTimeline, resolveLiveToolWaitersIfReady]);

  const waitForLiveToolTerminal = useCallback((toolId: string) => {
    return new Promise<void>((resolve) => {
      const existingTool = liveTimelineRef.current.find(
        (item): item is Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> =>
          item.kind === "tool" && item.id === toolId,
      );
      if (existingTool && existingTool.status !== "loading") {
        resolve();
        return;
      }

      let settled = false;
      const settle = (timedOut: boolean) => {
        if (settled) {
          return;
        }
        settled = true;

        const timeoutTimerId = liveToolTimeoutTimersRef.current.get(toolId);
        if (timeoutTimerId) {
          window.clearTimeout(timeoutTimerId);
          liveToolTimeoutTimersRef.current.delete(toolId);
        }

        liveToolWaiterResolversRef.current.delete(toolId);

        if (timedOut) {
          setLiveTimeline((current) =>
            current.map((item) => {
              if (item.kind !== "tool" || item.id !== toolId || item.status !== "loading") {
                return item;
              }

              const timeoutSummary = "Timed out after 15s waiting for tool_result.";
              return {
                ...item,
                status: "timeout",
                summaryLines: item.summaryLines.includes(timeoutSummary)
                  ? item.summaryLines
                  : [...item.summaryLines, timeoutSummary],
              };
            }),
          );
        }

        resolve();
      };

      liveToolWaiterResolversRef.current.set(toolId, () => settle(false));
      const timeoutTimerId = window.setTimeout(() => settle(true), TOOL_RESULT_TIMEOUT_MS);
      liveToolTimeoutTimersRef.current.set(toolId, timeoutTimerId);
    });
  }, []);

  const processLiveQueue = useCallback(async () => {
    if (liveQueueProcessingRef.current) {
      return;
    }

    const isHistorical = TERMINAL_SESSION_STATUSES.has(
      String(session?.status ?? "").trim().toLowerCase(),
    );

    const queueToken = liveQueueTokenRef.current;
    liveQueueProcessingRef.current = true;

    try {
      if (isHistorical) {
        const batch: DiagnosisModifiedTimelineItem[] = [];
        while (liveQueuedItemsRef.current.length > 0) {
          const queuedItem = liveQueuedItemsRef.current.shift();
          if (!queuedItem) {
            continue;
          }
          liveQueuedIdsRef.current.delete(queuedItem.id);
          const nextItem = latestLiveSourceByIdRef.current.get(queuedItem.id) ?? queuedItem;
          liveDisplayedIdsRef.current.add(nextItem.id);
          batch.push(nextItem);
        }
        if (batch.length > 0) {
          setLiveTimeline((current) => upsertTimelineItems(current, batch));
        }
        return;
      }

      while (liveQueuedItemsRef.current.length > 0 && queueToken === liveQueueTokenRef.current) {
        const queuedItem = liveQueuedItemsRef.current.shift();
        if (!queuedItem) {
          continue;
        }

        liveQueuedIdsRef.current.delete(queuedItem.id);
        const nextItem = latestLiveSourceByIdRef.current.get(queuedItem.id) ?? queuedItem;

        liveDisplayedIdsRef.current.add(nextItem.id);
        setLiveTimeline((current) => upsertTimelineItems(current, [nextItem]));

        if (nextItem.kind === "message" && nextItem.role === "assistant") {
          await waitForMessageStream(nextItem.id, nextItem.content);
          continue;
        }

        if (nextItem.kind === "tool" && nextItem.status === "loading") {
          await waitForLiveToolTerminal(nextItem.id);
        }
      }
    } finally {
      liveQueueProcessingRef.current = false;
    }
  }, [session?.status, waitForLiveToolTerminal, waitForMessageStream]);

  useEffect(() => {
    if (!hasLiveSession) {
      initializedLiveSessionIdRef.current = null;
      liveQueueTokenRef.current += 1;
      liveQueuedItemsRef.current = [];
      liveQueuedIdsRef.current = new Set();
      liveDisplayedIdsRef.current = new Set();
      latestLiveSourceByIdRef.current = new Map();
      clearLiveToolWaiters();
      setLiveTimeline([]);
      return;
    }

    const currentSessionId = activeSessionId ?? session?.session_id ?? routeSessionId;
    const sourceTimeline = liveTimelineSource;
    const sourceById = new Map(sourceTimeline.map((item) => [item.id, item]));
    latestLiveSourceByIdRef.current = sourceById;

    if (initializedLiveSessionIdRef.current !== currentSessionId) {
      initializedLiveSessionIdRef.current = currentSessionId;
      liveQueueTokenRef.current += 1;
      liveQueuedItemsRef.current = [];
      liveQueuedIdsRef.current = new Set();
      liveDisplayedIdsRef.current = new Set(sourceTimeline.map((item) => item.id));
      clearLiveToolWaiters();
      setLiveTimeline(sourceTimeline);
      return;
    }

    setLiveTimeline((current) =>
      current.flatMap((item) => {
        const latest = sourceById.get(item.id);
        if (!latest) {
          if (item.kind === "thinking" && item.status === "thinking") {
            const replacement = findCompletedThinkingReplacement(sourceTimeline, item);
            if (replacement) {
              liveDisplayedIdsRef.current.delete(item.id);
              liveDisplayedIdsRef.current.add(replacement.id);
              liveQueuedIdsRef.current.delete(replacement.id);
              return [replacement];
            }
            if (
              hasActiveThinkingStream &&
              activeStreamingThinkingItem &&
              (item.id === activeStreamingThinkingItem.id || isSameThinkingRound(item, activeStreamingThinkingItem))
            ) {
              return [item];
            }
          }
          if (isTransientLiveTimelineItem(item)) {
            return [item];
          }
          liveDisplayedIdsRef.current.delete(item.id);
          return [];
        }

        if (
          item.kind === "tool" &&
          latest.kind === "tool" &&
          item.status === "timeout" &&
          latest.status === "loading"
        ) {
          return [item];
        }

        return [latest];
      }),
    );

    const queuedItems = sourceTimeline.filter(
      (item) =>
        !liveDisplayedIdsRef.current.has(item.id) &&
        !liveQueuedIdsRef.current.has(item.id),
    );

    if (queuedItems.length > 0) {
      queuedItems.forEach((item) => {
        liveQueuedIdsRef.current.add(item.id);
      });
      liveQueuedItemsRef.current.push(...queuedItems);
      void processLiveQueue();
    }
  }, [
    activeSessionId,
    clearLiveToolWaiters,
    hasLiveSession,
    hasActiveThinkingStream,
    activeStreamingThinkingItem,
    liveTimelineSource,
    processLiveQueue,
    routeSessionId,
    session?.session_id,
  ]);

  const waitForDemoDelay = useCallback((delayMs: number, runToken: number) => {
    if (delayMs <= 0 || runToken !== demoRunTokenRef.current) {
      return Promise.resolve();
    }

    return new Promise<void>((resolve) => {
      const timerId = window.setTimeout(() => {
        resolve();
      }, delayMs);
      demoTimerRef.current.push(timerId);
    });
  }, []);

  const startDemo = useCallback(
    (prompt: string) => {
      const normalizedPrompt = prompt.trim();
      if (!normalizedPrompt) {
        return;
      }

      demoRunTokenRef.current += 1;
      const runToken = demoRunTokenRef.current;

      clearDemoTimers();
      clearPendingMessageStreams();
      clearPendingThinkingStreams();
      clearDemoToolLoadingStates();

      const scenario = buildDiagnosisModifiedDemoScenario(normalizedPrompt);
      setDemoTimeline(scenario.initialTimeline);
      setDemoCandidates([]);
      setDemoCandidateSnapshots([]);
      setDemoHypotheses([]);
      setDemoPropagationChain([]);
      setDemoSummary(undefined);
      setDemoPlan(undefined);
      setDemoSession(undefined);
      setDemoApprovalState("pending");
      setDemoActionFeedback(null);
      setLastDemoPrompt(normalizedPrompt);

      void (async () => {
        let previousDelay = 0;
        const activeDemoThinkingIds = new Set<string>();
        const activeDemoToolIds = new Set<string>();
        const knownDemoThinkingIds = new Set<string>();
        const knownDemoToolIds = new Set<string>();
        const deferredDemoEvents: DiagnosisModifiedDemoEvent[] = [];

        const canProcessDemoEvent = (event: DiagnosisModifiedDemoEvent) => {
          if (activeDemoThinkingIds.size > 0) {
            return event.type === "update_thinking" && activeDemoThinkingIds.has(event.targetId);
          }

          if (activeDemoToolIds.size > 0) {
            return event.type === "update_tool" && activeDemoToolIds.has(event.targetId);
          }

          if (event.type === "update_thinking") {
            return knownDemoThinkingIds.has(event.targetId);
          }

          if (event.type === "update_tool") {
            return knownDemoToolIds.has(event.targetId);
          }

          return true;
        };

        const applyDemoEvent = async (event: DiagnosisModifiedDemoEvent): Promise<boolean> => {
          if (event.type === "append") {
            if (event.item.kind === "thinking" && event.item.status === "thinking") {
              knownDemoThinkingIds.add(event.item.id);
              activeDemoThinkingIds.add(event.item.id);
              demoThinkingStartedAtRef.current.set(event.item.id, Date.now());
              setDemoTimeline((current) => [...current, event.item]);

              await waitForThinkingStream(event.item.id, event.item.content);

              const startedAt = demoThinkingStartedAtRef.current.get(event.item.id);
              const elapsedSec = startedAt
                ? Math.max(1, Math.round((Date.now() - startedAt) / 1000))
                : estimateThoughtDurationSecFromContent(event.item.content);

              setDemoTimeline((current) =>
                current.map((item) => {
                  if (item.kind !== "thinking" || item.id !== event.item.id) {
                    return item;
                  }

                  return {
                    ...item,
                    status: "completed",
                    thoughtDurationSec: elapsedSec,
                  };
                }),
              );
              activeDemoThinkingIds.delete(event.item.id);
              demoThinkingStartedAtRef.current.delete(event.item.id);
              return true;
            }

            if (event.item.kind === "message" && event.item.role === "assistant") {
              const { thinking, conclusion } = splitThinkingAndConclusion(event.item.content);

              if (thinking) {
                const thoughtId = `${event.item.id}-thought`;
                knownDemoThinkingIds.add(thoughtId);
                activeDemoThinkingIds.add(thoughtId);
                demoThinkingStartedAtRef.current.set(thoughtId, Date.now());

                setDemoTimeline((current) => [
                  ...current,
                  {
                    id: thoughtId,
                    kind: "thinking",
                    title: "Reasoning",
                    content: thinking,
                    timestamp: event.item.timestamp,
                    status: "thinking",
                  },
                ]);

                await waitForThinkingStream(thoughtId, thinking);

                const startedAt = demoThinkingStartedAtRef.current.get(thoughtId);
                const elapsedSec = startedAt
                  ? Math.max(1, Math.round((Date.now() - startedAt) / 1000))
                  : estimateThoughtDurationSecFromContent(thinking);

                setDemoTimeline((current) =>
                  current.map((item) => {
                    if (item.kind !== "thinking" || item.id !== thoughtId) {
                      return item;
                    }

                    return {
                      ...item,
                      status: "completed",
                      thoughtDurationSec: elapsedSec,
                    };
                  }),
                );
                activeDemoThinkingIds.delete(thoughtId);
                demoThinkingStartedAtRef.current.delete(thoughtId);
              }

              if (conclusion.length > 0) {
                const conclusionItem =
                  thinking !== null
                    ? { ...event.item, id: `${event.item.id}-answer`, content: conclusion }
                    : { ...event.item, content: conclusion };

                setDemoTimeline((current) => [...current, conclusionItem]);
                await waitForMessageStream(conclusionItem.id, conclusionItem.content);
              }
              return true;
            }

            setDemoTimeline((current) => [...current, event.item]);

            if (event.item.kind === "thinking") {
              knownDemoThinkingIds.add(event.item.id);
              if (event.item.status === "thinking") {
                activeDemoThinkingIds.add(event.item.id);
              } else {
                activeDemoThinkingIds.delete(event.item.id);
              }
            }

            if (event.item.kind === "tool") {
              knownDemoToolIds.add(event.item.id);
              if (event.item.status === "loading") {
                activeDemoToolIds.add(event.item.id);
                demoToolLoadingStartedAtRef.current.set(event.item.id, Date.now());
              } else {
                activeDemoToolIds.delete(event.item.id);
                demoToolLoadingStartedAtRef.current.delete(event.item.id);
              }
            }
            return true;
          }

          if (event.type === "update_candidates") {
            setDemoCandidates(event.candidates);
            setDemoCandidateSnapshots((current) => [
              ...current,
              {
                id: `demo-candidate-snapshot-${event.delayMs}-${current.length + 1}`,
                timestamp:
                  [...demoTimeline.map((item) => item.timestamp)].sort((left, right) => Date.parse(right) - Date.parse(left))[0] ??
                  new Date().toISOString(),
                candidates: event.candidates,
              },
            ]);
            return true;
          }

          if (event.type === "update_tool") {
            if (!knownDemoToolIds.has(event.targetId)) {
              return false;
            }

            const toolLoadingStartedAt = demoToolLoadingStartedAtRef.current.get(event.targetId);
            if (typeof toolLoadingStartedAt === "number") {
              const elapsedMs = Date.now() - toolLoadingStartedAt;
              const remainingMs = Math.max(0, DEMO_MIN_TOOL_LOADING_DWELL_MS - elapsedMs);
              await waitForDemoDelay(remainingMs, runToken);

              if (runToken !== demoRunTokenRef.current) {
                return false;
              }
            }

            setDemoTimeline((current) =>
              current.map((item) => {
                if (item.kind !== "tool" || item.id !== event.targetId) {
                  return item;
                }

                return {
                  ...item,
                  status: "success",
                  summaryLines: event.summaryLines,
                  rawResult: event.rawResult,
                };
              }),
            );
            activeDemoToolIds.delete(event.targetId);
            demoToolLoadingStartedAtRef.current.delete(event.targetId);
            return true;
          }

          if (event.type === "update_thinking") {
            if (!knownDemoThinkingIds.has(event.targetId)) {
              return false;
            }

            const startedAt = demoThinkingStartedAtRef.current.get(event.targetId);
            const elapsedSec = startedAt
              ? Math.max(1, Math.round((Date.now() - startedAt) / 1000))
              : undefined;

            setDemoTimeline((current) =>
              current.map((item) => {
                if (item.kind !== "thinking" || item.id !== event.targetId) {
                  return item;
                }

                return {
                  ...item,
                  status: event.status,
                  thoughtDurationSec:
                    event.status === "completed"
                      ? event.thoughtDurationSec ??
                        elapsedSec ??
                        item.thoughtDurationSec ??
                        estimateThoughtDurationSecFromContent(item.content)
                      : item.thoughtDurationSec,
                };
              }),
            );

            if (event.status === "completed") {
              activeDemoThinkingIds.delete(event.targetId);
              demoThinkingStartedAtRef.current.delete(event.targetId);
            } else {
              activeDemoThinkingIds.add(event.targetId);
            }
            return true;
          }

          setDemoCandidates(scenario.candidates);
          setDemoCandidateSnapshots((current) =>
            current.length > 0
              ? current
              : [
                  {
                    id: `demo-candidate-snapshot-final-${scenario.session?.session_id ?? "complete"}`,
                    timestamp:
                      scenario.session?.alert.starts_at ??
                      new Date().toISOString(),
                    candidates: scenario.candidates,
                  },
                ],
          );
          setDemoHypotheses(scenario.hypotheses ?? []);
          setDemoPropagationChain(scenario.propagationChain ?? []);
          setDemoSummary(scenario.summary);
          setDemoPlan(scenario.plan);
          setDemoSession(scenario.session);
          clearDemoToolLoadingStates();
          return true;
        };

        const flushDeferredDemoEventsIfUnblocked = async () => {
          while (deferredDemoEvents.length > 0) {
            if (runToken !== demoRunTokenRef.current) {
              return;
            }

            const deferredEvent = deferredDemoEvents[0];
            if (!deferredEvent || !canProcessDemoEvent(deferredEvent)) {
              return;
            }

            deferredDemoEvents.shift();
            const applied = await applyDemoEvent(deferredEvent);
            if (!applied) {
              deferredDemoEvents.unshift(deferredEvent);
              return;
            }
          }
        };

        for (const event of scenario.events) {
          if (runToken !== demoRunTokenRef.current) {
            return;
          }

          const deltaMs = Math.max(0, event.delayMs - previousDelay);
          previousDelay = event.delayMs;
          await waitForDemoDelay(Math.round(deltaMs * DEMO_EVENT_SLOWDOWN), runToken);

          if (runToken !== demoRunTokenRef.current) {
            return;
          }

          if (!canProcessDemoEvent(event)) {
            deferredDemoEvents.push(event);
            continue;
          }

          const applied = await applyDemoEvent(event);
          if (!applied) {
            deferredDemoEvents.push(event);
            continue;
          }

          await flushDeferredDemoEventsIfUnblocked();
        }

        await flushDeferredDemoEventsIfUnblocked();
      })();
    },
    [clearDemoTimers, clearDemoToolLoadingStates, clearPendingMessageStreams, clearPendingThinkingStreams, waitForDemoDelay, waitForMessageStream, waitForThinkingStream],
  );

  const handleStartDemo = useCallback(() => {
    if (hasLiveSession) {
      return;
    }

    startDemo(lastDemoPrompt || DEMO_DEFAULT_PROMPT);
  }, [hasLiveSession, lastDemoPrompt, startDemo]);

  const handleRealtimeEvent = useCallback(
    (event: Parameters<typeof applyEvent>[0]) => {
      applyEvent(event);
    },
    [applyEvent],
  );

  const websocketEnabled =
    shouldBootstrapLiveSession &&
    import.meta.env.VITE_WS_ENABLED === "true" &&
    Boolean(activeSessionId) &&
    !isTerminalLiveSession;
  const websocketUrl = useMemo(
    () =>
      buildBackendWsUrl(`/ws/thinking-trace/${activeSessionId ?? "pending"}`, {
        token: import.meta.env.VITE_API_TOKEN ?? "",
      }),
    [activeSessionId],
  );

  const ws = useWebSocket(websocketUrl, handleRealtimeEvent, {
    enabled: websocketEnabled,
  });

  useEffect(() => {
    setConnectionState(ws.state);
  }, [setConnectionState, ws.state]);

  const activeTimeline = hasLiveSession ? liveTimeline : demoTimeline;
  const activeTraceItems = useMemo(
    () => activeTimeline.filter((item) => !(item.kind === "message" && item.role === "user")),
    [activeTimeline],
  );
  const visibleTraceItems = useMemo(
    () =>
      hasLiveSession
        ? activeTraceItems
        : activeTraceItems.filter((item) => !isDemoNarrativeCard(item)),
    [activeTraceItems, hasLiveSession],
  );
  const activeTraceItemsBeforeStatusSync = useMemo(
    () => visibleTraceItems.filter((item) => item.kind !== "status_sync"),
    [visibleTraceItems],
  );
  const activeStatusSyncItems = useMemo(
    () =>
      visibleTraceItems.filter(
        (item): item is Extract<DiagnosisModifiedTimelineItem, { kind: "status_sync" }> => item.kind === "status_sync",
      ),
    [visibleTraceItems],
  );
  const activeCandidates = hasLiveSession ? liveView.candidates : demoCandidates;
  const activeCandidateSnapshots = useMemo(() => {
    if (hasLiveSession) {
      if (liveView.candidates.length === 0) {
        return [];
      }

      return [
        {
          id: `live-candidate-snapshot-${activeSessionId ?? session?.session_id ?? "current"}`,
          timestamp:
            [...liveTimelineSource.map((item) => item.timestamp)].sort((left, right) => Date.parse(right) - Date.parse(left))[0] ??
            new Date().toISOString(),
          candidates: liveView.candidates,
        },
      ];
    }

    return demoCandidateSnapshots;
  }, [
    activeSessionId,
    demoCandidateSnapshots,
    hasLiveSession,
    liveView.candidates,
    liveTimelineSource,
    session?.session_id,
  ]);
  const activeSummary = hasLiveSession ? liveView.summary : demoSummary;
  const activePlan = hasLiveSession ? liveView.plan : demoPlan;
  const activeSession = hasLiveSession ? session : demoSession;
  const activeEvents = hasLiveSession ? events : demoEvents;
  const activeLocalAuditRecords = hasLiveSession ? localAuditRecords : demoLocalAuditRecords;
  const activeTopologyContext = hasLiveSession ? topologyContext : null;

  const reportView = useMemo(
    () =>
      buildDiagnosisModifiedReportView({
        session: activeSession,
        timeline: activeTimeline,
        candidates: activeCandidates,
        candidateSnapshots: activeCandidateSnapshots,
        summary: activeSummary,
        plan: activePlan,
        events: activeEvents,
        localAuditRecords: activeLocalAuditRecords,
        topologyContext: activeTopologyContext,
        relationScope: "direct_only",
      }),
    [
      activeCandidates,
      activeCandidateSnapshots,
      activeEvents,
      activeLocalAuditRecords,
      activePlan,
      activeSession,
      activeSummary,
      activeTimeline,
      activeTopologyContext,
    ],
  );

  const flowRemediationEntry = useMemo(() => {
    if (!activeSession?.session_id || activeTimeline.length === 0) {
      return null;
    }

    return (
      <FlowRemediationEntry
        sessionId={activeSession.session_id}
        status={activeSession.status}
      />
    );
  }, [activeSession, activeTimeline.length]);


  const inlineApprovalSurface = useMemo(() => {
    if (!activePlan || !activeSession) {
      return null;
    }

    const normalizedStatus = String(activeSession.status ?? "").trim().toLowerCase();
    const shouldShowPendingSurface = hasLiveSession
      ? normalizedStatus === "approval_required" || normalizedStatus === "awaiting_approval"
      : normalizedStatus === "approval_required";

    const resolution: ApprovalPlanResolution | null = hasLiveSession
      ? liveApprovalResolution
      : demoApprovalState === "approved" && normalizedStatus === "approved"
        ? {
            label: "\u5df2\u6279\u51c6",
            tone: "success" as const,
            description:
              demoActionFeedback ??
              "\u5f53\u524d\u4fee\u590d\u65b9\u6848\u5df2\u5728\u8bca\u65ad\u9875\u5185\u6279\u51c6\u3002",
          }
        : null;

    if (!shouldShowPendingSurface && !resolution) {
      return null;
    }

    return (
      <ApprovalPlanCard
        approvalBlockReason={resolution ? undefined : hasLiveSession ? approvalBlockReason : undefined}
        canApprove={hasLiveSession ? canApprove : demoApprovalState === "pending"}
        isSubmitting={hasLiveSession ? isApprovingPlan : demoApprovalState === "updating"}
        onApprove={hasLiveSession ? () => void handleApproveLivePlan() : handleApproveDemoPlan}
        plan={activePlan}
        resolution={resolution}
      />
    );
  }, [
    activePlan,
    activeSession,
    approvalBlockReason,
    canApprove,
    demoActionFeedback,
    demoApprovalState,
    handleApproveDemoPlan,
    handleApproveLivePlan,
    hasLiveSession,
    isApprovingPlan,
    liveApprovalResolution,
  ]);

  const actionGeneratedStep = useMemo(() => {
    const remediationReady =
      reportView.remediation.state === "ready" ||
      reportView.progress.activeStepId === "remediation" ||
      Boolean(inlineApprovalSurface);
    if (!remediationReady || (!flowRemediationEntry && !inlineApprovalSurface)) {
      return null;
    }

    return (
      <ActionGeneratedStep plan={activePlan} status={activeSession?.status}>
        {flowRemediationEntry}
        {inlineApprovalSurface}
      </ActionGeneratedStep>
    );
  }, [
    activePlan,
    activeSession?.status,
    flowRemediationEntry,
    inlineApprovalSurface,
    reportView.progress.activeStepId,
    reportView.remediation.state,
  ]);
  const firstRemediationResponseIndex = useMemo(
    () =>
      activeTraceItemsBeforeStatusSync.findIndex(
        (item) =>
          item.kind === "message" &&
          item.role === "assistant" &&
          item.sourceEventType === "remediation_progress",
      ),
    [activeTraceItemsBeforeStatusSync],
  );
  const nextActionIndex = useMemo(
    () =>
      activeTraceItemsBeforeStatusSync.findIndex(
        (item) => item.kind === "message" && item.role === "assistant" && item.label === "Next action",
      ),
    [activeTraceItemsBeforeStatusSync],
  );
  const actionGeneratedInsertIndex = useMemo(() => {
    if (!actionGeneratedStep) {
      return -1;
    }
    if (firstRemediationResponseIndex >= 0) {
      return firstRemediationResponseIndex;
    }
    if (hasLiveSession && nextActionIndex >= 0) {
      return nextActionIndex + 1;
    }
    if (hasLiveSession) {
      return activeTraceItemsBeforeStatusSync.length;
    }
    return -1;
  }, [
    actionGeneratedStep,
    activeTraceItemsBeforeStatusSync.length,
    firstRemediationResponseIndex,
    hasLiveSession,
    nextActionIndex,
  ]);
  const shouldRenderActionGeneratedInline =
    Boolean(actionGeneratedStep) && actionGeneratedInsertIndex >= 0;
  const shouldRenderActionGeneratedAfterInlineTail =
    shouldRenderActionGeneratedInline && actionGeneratedInsertIndex === activeTraceItemsBeforeStatusSync.length;
  const shouldRenderActionGeneratedAtTail =
    Boolean(actionGeneratedStep) && !hasLiveSession && !shouldRenderActionGeneratedInline;

  useEffect(() => {
    const container = timelineScrollRef.current;
    if (!container) {
      return;
    }

    const shouldForceFollow = forceTimelineAutoFollowRef.current;
    if (!shouldForceFollow && !timelineAutoFollowRef.current) {
      return;
    }

    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    forceTimelineAutoFollowRef.current = false;
    timelineAutoFollowRef.current = true;
  }, [activeTimeline]);

  const inlineError = useMemo(
    () => (shouldBootstrapLiveSession ? resolveInlineError(error) : null),
    [error, shouldBootstrapLiveSession],
  );

  return (
    <div className="page-grid diagnosis-modified-page">
      <section className="diagnosis-modified-shell diagnosis-modified-shell--split-layout">
        <div
          className="diagnosis-modified-shell__body diagnosis-modified-shell__body--split"
          data-layout="trace-report"
          data-testid="diagnosis-modified-split-workspace"
        >
          <div
            aria-label="诊断轨迹"
            className="diagnosis-modified-main-pane"
            data-testid="diagnosis-modified-main-pane"
          >
            <header className="diagnosis-modified-pane-header diagnosis-modified-pane-header--trace">
              <div>
                <p>Thinking Trace</p>
                <h2>诊断轨迹</h2>
              </div>
              <span>实时推理链路</span>
            </header>
            <div
              className="diagnosis-modified-feed"
              data-testid="diagnosis-modified-timeline"
              onScroll={handleTimelineScroll}
              ref={timelineScrollRef}
            >
              {activeTimeline.length === 0 ? (
                <div className="diagnosis-modified-empty-state">
                  <div className="diagnosis-modified-empty-state__icon">
                    <AppIcon name="aiChat" size={18} />
                  </div>
                  <h2>Ready to Start Diagnosis and Remediation</h2>
                  {!hasLiveSession ? (
                    <button
                      className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary"
                      onClick={handleStartDemo}
                      type="button"
                    >
                      Start Demo
                    </button>
                  ) : null}
                </div>
              ) : (
                <ol className="diagnosis-modified-trace-list" data-testid="diagnosis-modified-trace-list">
                  {activeTraceItemsBeforeStatusSync.map((item, index) => (
                    <Fragment key={item.id}>
                      {shouldRenderActionGeneratedInline && index === actionGeneratedInsertIndex
                        ? actionGeneratedStep
                        : null}
                      {item.kind === "context_start" ? (
                        <ContextStartRow item={item} />
                      ) : item.kind === "message" ? (
                        <MessageRow
                          animate={item.role === "assistant" && activeStreamingMessageId === item.id}
                          item={item}
                          onStreamComplete={
                            item.role === "assistant" && activeStreamingMessageId === item.id
                              ? () => {
                                  resolveMessageStream(item.id);
                                }
                              : undefined
                          }
                        />
                      ) : item.kind === "thinking" ? (
                        <ThinkingBlock
                          animate={
                            item.status === "thinking" &&
                            ((!hasLiveSession) || item.id.startsWith(LIVE_CONTEXT_THINKING_ID_PREFIX))
                          }
                          item={item}
                          onStreamComplete={
                            item.status === "thinking" &&
                            ((!hasLiveSession) || item.id.startsWith(LIVE_CONTEXT_THINKING_ID_PREFIX))
                              ? () => {
                                  resolveThinkingStream(item.id);
                                }
                              : undefined
                          }
                        />
                      ) : (
                        <ToolCard item={item} />
                      )}
                    </Fragment>
                  ))}
                  {shouldRenderActionGeneratedAfterInlineTail ? actionGeneratedStep : null}

                  {shouldRenderActionGeneratedAtTail ? actionGeneratedStep : null}

                  {activeStatusSyncItems.map((item) => (
                    <StatusSyncRow item={item} key={item.id} />
                  ))}
                </ol>
              )}

              {inlineError ? (
                <div
                  className={cn(
                    "diagnosis-modified-inline-note",
                    inlineError.tone === "error"
                      ? "diagnosis-modified-inline-note--error"
                      : "diagnosis-modified-inline-note--warning",
                  )}
                >
                  {inlineError.message}
                </div>
              ) : null}
              <div ref={bottomRef} />
            </div>
          </div>

          <DiagnosisModifiedReportRail view={reportView} />
        </div>
      </section>
    </div>
  );
}
export default DiagnosisModifiedPage;
