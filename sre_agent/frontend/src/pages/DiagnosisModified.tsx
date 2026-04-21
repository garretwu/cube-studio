import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
const DEMO_THINKING_COLLAPSE_DELAY_MS = 800;
const DEMO_EVENT_SLOWDOWN = 4.5;
const DEMO_MIN_TOOL_LOADING_DWELL_MS = 3500;
const TOOL_RESULT_TIMEOUT_MS = 15_000;
const STREAM_COMPLETION_BUFFER_MS = 640;
const DEMO_DEFAULT_PROMPT = "Analyze auth-svc latency and error-rate spike in the past hour";
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
  return FLOW_REMEDIATION_ENTRY_STATUSES.has(String(status ?? "").trim().toLowerCase());
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
  const safeDuration = Math.max(1, Math.round(durationSec ?? 1));
  return `Thought for ${safeDuration} second${safeDuration === 1 ? "" : "s"}`;
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

type TraceStepKind = "thought" | "tool_call" | "observation" | "decision" | "action_generated";

const TRACE_STEP_META: Record<TraceStepKind, { label: string; glyph: string }> = {
  thought: { label: "Thought / 推理", glyph: "T" },
  tool_call: { label: "Tool Call / 工具调用", glyph: "C" },
  observation: { label: "Observation / 观察结果", glyph: "O" },
  decision: { label: "Decision / 形成判断", glyph: "D" },
  action_generated: { label: "Action Generated / 生成修复动作", glyph: "A" },
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
  meta,
  summary,
  children,
  className,
  testId,
}: {
  type: TraceStepKind;
  title: string;
  meta?: string;
  summary?: string;
  children?: ReactNode;
  className?: string;
  testId?: string;
}) {
  const typeMeta = TRACE_STEP_META[type];

  return (
    <li
      className={cn("diagnosis-modified-trace-step", `diagnosis-modified-trace-step--${type}`, className)}
      data-testid={testId}
    >
      <div className="diagnosis-modified-trace-step__rail" aria-hidden="true">
        <span>{typeMeta.glyph}</span>
      </div>
      <article className="diagnosis-modified-trace-step__body">
        <header className="diagnosis-modified-trace-step__header">
          <div>
            <span className="diagnosis-modified-trace-step__type">{typeMeta.label}</span>
            <h3 className="diagnosis-modified-trace-step__title">{title}</h3>
          </div>
          {meta ? <span className="diagnosis-modified-trace-step__meta">{meta}</span> : null}
        </header>
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

  useEffect(() => {
    if (isThinking) {
      setIsExpanded(true);
      return;
    }

    const timer = window.setTimeout(() => setIsExpanded(false), DEMO_THINKING_COLLAPSE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [isThinking, item.id]);

  const durationLabel = formatThoughtDurationLabel(item.thoughtDurationSec ?? estimateThoughtDurationSecFromContent(item.content));

  return (
    <TraceStepFrame
      meta={isThinking ? "Thinking..." : durationLabel}
      title={isThinking ? item.title || "正在推理" : "推理完成"}
      type="thought"
    >
      <div className="diagnosis-modified-process-row">
        <div className="diagnosis-modified-process-row__body">
        <button
          className={cn("diagnosis-modified-thinking__toggle", !isThinking && "diagnosis-modified-thinking__toggle--interactive")}
          onClick={() => {
            if (!isThinking) {
              setIsExpanded((current) => !current);
            }
          }}
          type="button"
        >
          <span className="diagnosis-modified-thinking__icon" aria-hidden="true">
            {isThinking ? <span className="diagnosis-modified-thinking__pulse" /> : <span className={cn("diagnosis-modified-thinking__chevron", isExpanded && "diagnosis-modified-thinking__chevron--expanded")} />}
          </span>
          <span className="diagnosis-modified-thinking__label-wrap">
            {isThinking ? (
              <span className="diagnosis-modified-thinking__label-tag" aria-hidden="true">
                <AppIcon name="spark" size={11} />
              </span>
            ) : null}
          <span className={cn("diagnosis-modified-thinking__label", isThinking && "diagnosis-modified-thinking__label--thinking")}>
              {isThinking ? item.title || "Thinking..." : "查看推理详情"}
            </span>
          </span>
          {item.toolName ? <span className="diagnosis-modified-thinking__tool">{item.toolName}</span> : null}
        </button>

        {isExpanded ? (
          <div className="diagnosis-modified-thinking__panel">
            <div className="diagnosis-modified-thinking__content">
              <p>
                <StreamingText animate={animate && isThinking} onComplete={animate && isThinking ? onStreamComplete : undefined} text={item.content} />
              </p>
            </div>
          </div>
        ) : null}
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
      meta={getRemediationEntryLabel(status)}
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

  const bottomRef = useRef<HTMLDivElement | null>(null);
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
    bootstrapStatus,
    traceStatus,
    error,
    isApprovingPlan,
    canApprove,
    approvalBlockReason,
    currentPlanVersion,
    approvePlan,
    bootstrapSession,
    applyEvent,
    setConnectionState,
  } = useDiagnosisStore();

  const liveView = useMemo(() => buildDiagnosisModifiedLiveView(session, messages), [messages, session]);
  const hasLiveSession =
    shouldBootstrapLiveSession && bootstrapStatus === "ready" && Boolean(session) && Boolean(activeSessionId);

  useEffect(() => {
    if (!shouldBootstrapLiveSession) {
      return;
    }
    void bootstrapSession(routeSessionId);
  }, [bootstrapSession, routeSessionId, shouldBootstrapLiveSession]);

  useEffect(() => {
    setLiveApprovalResolution(null);
  }, [currentPlanVersion, session?.session_id]);

  const handleApproveDemoPlan = useCallback(() => {
    setDemoApprovalState("approved");
    setDemoActionFeedback("\u5df2\u5728\u8bca\u65ad\u9875\u5185\u786e\u8ba4\u8be5\u4fee\u590d\u65b9\u6848\uff0c\u53ef\u7ee7\u7eed\u4ece\u6d41\u7a0b\u6216\u53f3\u4fa7\u67e5\u770b\u4fee\u590d\u8fdb\u5c55\u3002");
  }, []);

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
    [clearDemoTimers, clearDemoToolLoadingStates, clearLiveToolWaiters, clearPendingMessageStreams, clearPendingThinkingStreams],
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

    const queueToken = liveQueueTokenRef.current;
    liveQueueProcessingRef.current = true;

    try {
      while (liveQueuedItemsRef.current.length > 0 && queueToken === liveQueueTokenRef.current) {
        const queuedItem = liveQueuedItemsRef.current.shift();
        if (!queuedItem) {
          continue;
        }

        liveQueuedIdsRef.current.delete(queuedItem.id);
        const nextItem = latestLiveSourceByIdRef.current.get(queuedItem.id) ?? queuedItem;

        liveDisplayedIdsRef.current.add(nextItem.id);
        setLiveTimeline((current) => [...current, nextItem]);

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
  }, [waitForLiveToolTerminal, waitForMessageStream]);

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
    const sourceTimeline = liveView.timeline;
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
      current.map((item) => {
        const latest = sourceById.get(item.id);
        if (!latest) {
          return item;
        }

        if (
          item.kind === "tool" &&
          latest.kind === "tool" &&
          item.status === "timeout" &&
          latest.status === "loading"
        ) {
          return item;
        }

        return latest;
      }),
    );

    const queuedItems = sourceTimeline.filter(
      (item) => !liveDisplayedIdsRef.current.has(item.id) && !liveQueuedIdsRef.current.has(item.id),
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
    liveView.timeline,
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
    shouldBootstrapLiveSession && import.meta.env.VITE_WS_ENABLED === "true" && Boolean(activeSessionId);
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
    () =>
      activeTimeline.filter(
        (item, index) => !(index === 0 && item.kind === "message" && item.role === "user"),
      ),
    [activeTimeline],
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
            [...liveView.timeline.map((item) => item.timestamp)].sort((left, right) => Date.parse(right) - Date.parse(left))[0] ??
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
    liveView.timeline,
    session?.session_id,
  ]);
  const activeSummary = hasLiveSession ? liveView.summary : demoSummary;
  const activePlan = hasLiveSession ? liveView.plan : demoPlan;
  const activeSession = hasLiveSession ? session : demoSession;
  const activeEvents = hasLiveSession ? events : demoEvents;
  const activeLocalAuditRecords = hasLiveSession ? localAuditRecords : demoLocalAuditRecords;

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
      : demoApprovalState === "approved"
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
    if (!flowRemediationEntry && !inlineApprovalSurface) {
      return null;
    }

    return (
      <ActionGeneratedStep plan={activePlan} status={activeSession?.status}>
        {flowRemediationEntry}
        {inlineApprovalSurface}
      </ActionGeneratedStep>
    );
  }, [activePlan, activeSession?.status, flowRemediationEntry, inlineApprovalSurface]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
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
                <span
                  className="diagnosis-modified-pane-header__sync-stage"
                  data-stage-id={reportView.progress.activeStepId}
                  data-testid="diagnosis-modified-trace-sync-stage"
                >
                  {getTraceSyncStageLabel(reportView.progress.activeStepId)}
                </span>
                <h2>诊断轨迹</h2>
              </div>
              <span>实时推理链路</span>
            </header>
            <div className="diagnosis-modified-feed" data-testid="diagnosis-modified-timeline">
              {activeTimeline.length === 0 ? (
                <div className="diagnosis-modified-empty-state">
                  <div className="diagnosis-modified-empty-state__icon">
                    <AppIcon name="aiChat" size={18} />
                  </div>
                  <h2>Ready to Start Diagnosis and Remediation</h2>
                  <p>Inspect reasoning and tool calls on the left; the report on the right stays synchronized.</p>
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
                  {activeTraceItems.map((item) => {
                    if (item.kind === "message") {
                      const shouldAnimateAssistantMessage =
                        item.role === "assistant" && activeStreamingMessageId === item.id;

                      return (
                        <MessageRow
                          animate={shouldAnimateAssistantMessage}
                          item={item}
                          key={item.id}
                          onStreamComplete={
                            shouldAnimateAssistantMessage
                              ? () => {
                                  resolveMessageStream(item.id);
                                }
                              : undefined
                          }
                        />
                      );
                    }

                    if (item.kind === "thinking") {
                      const shouldAnimateThinking = !hasLiveSession && item.status === "thinking";
                      return (
                        <ThinkingBlock
                          animate={shouldAnimateThinking}
                          item={item}
                          key={item.id}
                          onStreamComplete={
                            shouldAnimateThinking
                              ? () => {
                                  resolveThinkingStream(item.id);
                                }
                              : undefined
                          }
                        />
                      );
                    }

                    return <ToolCard item={item} key={item.id} />;
                  })}

                  {actionGeneratedStep}
                </ol>
              )}

              {hasLiveSession && traceStatus === "empty" ? (
                <div className="diagnosis-modified-inline-note">
                  Live session has not produced new trace entries yet.
                </div>
              ) : null}

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
