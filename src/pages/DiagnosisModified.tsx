import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { useParams } from "react-router-dom";

import { buildBackendWsUrl } from "../api/ws";
import { AppIcon } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";
import { formatSeverity, formatWorkflowStatus } from "../utils/display";
import { formatTimestamp } from "../utils/format";
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

type BadgeTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "info";
const DEMO_TEXT_SPEED_MS = 40;
const DEMO_THINKING_COLLAPSE_DELAY_MS = 800;
const DEMO_EVENT_SLOWDOWN = 4.5;
const DEMO_MIN_TOOL_LOADING_DWELL_MS = 3500;
const TOOL_RESULT_TIMEOUT_MS = 15_000;
const STREAM_COMPLETION_BUFFER_MS = 640;

function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}
const THINK_TAG_BLOCK_PATTERN = /<think>([\s\S]*?)<\/think>/i;

function estimateThoughtDurationSecFromContent(content: string) {
  return Math.max(1, Math.round((content.length * DEMO_TEXT_SPEED_MS) / 1000));
}

function formatThoughtDurationLabel(durationSec?: number) {
  const safeDuration = Math.max(1, Math.round(durationSec ?? 1));
  return `Thought for ${safeDuration} second${safeDuration === 1 ? "" : "s"}`;
}

function splitThinkingAndConclusion(content: string): {
  thinking: string | null;
  conclusion: string;
} {
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

function resolveInlineError(
  error?: string,
): { tone: "error" | "warning"; message: string } | null {
  if (!error) {
    return null;
  }
  if (/status\s+404/i.test(error)) {
    return {
      tone: "warning",
      message:
        "The session could not be found (404). It may have expired or the backend detail endpoint is unavailable. You can still type below to start a new diagnosis.",
    };
  }
  return { tone: "error", message: error };
}

function getDiagnosisStatusBadgeTone(status?: string): BadgeTone {
  switch (status) {
    case "resolved":
    case "closed":
      return "success";
    case "error":
    case "failed":
    case "escalated":
      return "danger";
    case "diagnosing":
    case "remediating":
    case "validating":
    case "testing":
      return "warning";
    case "awaiting_approval":
    case "approval_required":
      return "info";
    case "diagnosed":
    case "approved":
    case "re_diagnosed":
    case "proposed_fix_ready":
      return "accent";
    default:
      return "neutral";
  }
}

function getSeverityBadgeTone(severity?: string): BadgeTone {
  switch (severity) {
    case "critical":
      return "danger";
    case "warning":
      return "warning";
    case "info":
      return "info";
    default:
      return "neutral";
  }
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

function ToneBadge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: BadgeTone;
}) {
  return (
    <span
      className={cn(
        "diagnosis-modified-badge",
        `diagnosis-modified-badge--${tone}`,
      )}
    >
      {children}
    </span>
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
  const displayedText = useProgressiveText(
    text,
    animate,
    DEMO_TEXT_SPEED_MS,
    onComplete,
  );
  const showCaret = animate && displayedText.length < text.length;

  return (
    <span className={className}>
      {displayedText}
      {showCaret ? (
        <span className="diagnosis-modified-caret" aria-hidden="true" />
      ) : null}
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
    <article
      className={cn(
        "diagnosis-modified-message-row",
        isUser && "diagnosis-modified-message-row--user",
      )}
      title={hoverTime}
    >
      <div className="diagnosis-modified-message-row__body">
        <span
          className="diagnosis-modified-message-row__hover-time"
          aria-hidden="true"
        >
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
    </article>
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

    const timer = window.setTimeout(
      () => setIsExpanded(false),
      DEMO_THINKING_COLLAPSE_DELAY_MS,
    );
    return () => window.clearTimeout(timer);
  }, [isThinking, item.id]);

  return (
    <article className="diagnosis-modified-process-row">
      <div className="diagnosis-modified-process-row__body">
        <button
          className={cn(
            "diagnosis-modified-thinking__toggle",
            !isThinking && "diagnosis-modified-thinking__toggle--interactive",
          )}
          onClick={() => {
            if (!isThinking) {
              setIsExpanded((current) => !current);
            }
          }}
          type="button"
        >
          <span
            className="diagnosis-modified-thinking__icon"
            aria-hidden="true"
          >
            {isThinking ? (
              <span className="diagnosis-modified-thinking__pulse" />
            ) : (
              <span
                className={cn(
                  "diagnosis-modified-thinking__chevron",
                  isExpanded &&
                    "diagnosis-modified-thinking__chevron--expanded",
                )}
              />
            )}
          </span>
          <span className="diagnosis-modified-thinking__label-wrap">
            {isThinking ? (
              <span
                className="diagnosis-modified-thinking__label-tag"
                aria-hidden="true"
              >
                <AppIcon name="spark" size={11} />
              </span>
            ) : null}
            <span
              className={cn(
                "diagnosis-modified-thinking__label",
                isThinking && "diagnosis-modified-thinking__label--thinking",
              )}
            >
              {isThinking
                ? "Thinking..."
                : formatThoughtDurationLabel(
                    item.thoughtDurationSec ??
                      estimateThoughtDurationSecFromContent(item.content),
                  )}
            </span>
          </span>
          {item.toolName ? (
            <span className="diagnosis-modified-thinking__tool">
              {item.toolName}
            </span>
          ) : null}
        </button>

        {isExpanded ? (
          <div className="diagnosis-modified-thinking__panel">
            <div className="diagnosis-modified-thinking__content">
              <p>
                <StreamingText
                  animate={animate && isThinking}
                  onComplete={
                    animate && isThinking ? onStreamComplete : undefined
                  }
                  text={item.content}
                />
              </p>
            </div>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function ToolCard({
  item,
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }>;
}) {
  const [isExpanded, setIsExpanded] = useState(item.status === "loading");
  const hasDetails =
    Object.keys(item.params).length > 0 || item.summaryLines.length > 0;

  useEffect(() => {
    if (item.status === "loading") {
      setIsExpanded(true);
      return;
    }

    setIsExpanded(false);
  }, [item.id, item.status]);

  return (
    <article className="diagnosis-modified-process-row diagnosis-modified-process-row--tool">
      <div className="diagnosis-modified-process-row__body">
        <button
          className={cn(
            "diagnosis-modified-tool-card",
            `diagnosis-modified-tool-card--${item.status}`,
            hasDetails &&
              item.status !== "loading" &&
              "diagnosis-modified-tool-card--interactive",
          )}
          onClick={() => {
            if (hasDetails && item.status !== "loading") {
              setIsExpanded((current) => !current);
            }
          }}
          type="button"
        >
          <div className="diagnosis-modified-tool-card__header">
            <div
              className="diagnosis-modified-tool-card__status-icon"
              aria-hidden="true"
            >
              <span
                className={cn(
                  "diagnosis-modified-tool-card__status-indicator",
                  `diagnosis-modified-tool-card__status-indicator--${item.status}`,
                )}
              />
            </div>
            <div className="diagnosis-modified-tool-card__header-copy">
              <strong>{item.toolName}</strong>
              {Object.keys(item.params).length > 0 ? (
                <span>{JSON.stringify(item.params)}</span>
              ) : null}
            </div>
            <span className="diagnosis-modified-tool-card__status-label">
              {item.status}
            </span>
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
                    <pre className="diagnosis-modified-tool-card__params">
                      {JSON.stringify(item.params, null, 2)}
                    </pre>
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
    </article>
  );
}

function RCAReportCard({
  summary,
  candidates,
  hypotheses,
  propagationChain,
}: {
  summary: DiagnosisModifiedSummaryView;
  candidates: DiagnosisModifiedCandidateView[];
  hypotheses?: DiagnosisModifiedHypothesisView[];
  propagationChain?: DiagnosisModifiedPropagationStepView[];
}) {
  const primaryCandidate = candidates[0];
  const hypothesisRows = hypotheses ?? [];
  const chainRows = propagationChain ?? [];
  const rootCauseEntities =
    summary.rootCauseEntities && summary.rootCauseEntities.length > 0
      ? summary.rootCauseEntities
      : (primaryCandidate?.entities ?? []);

  return (
    <section className="diagnosis-modified-report-card">
      <header className="diagnosis-modified-report-card__header">
        <div>
          <div className="diagnosis-modified-report-card__eyebrow">
            <ToneBadge tone="neutral">{"\u6839\u56e0\u8bca\u65ad"}</ToneBadge>
            {summary.priorityLabel ? (
              <ToneBadge tone="warning">{summary.priorityLabel}</ToneBadge>
            ) : null}
            <ToneBadge tone={summary.certaintyTone}>
              {summary.certaintyLabel}
            </ToneBadge>
          </div>
          <h3 className="diagnosis-modified-report-card__title">
            {summary.title}
          </h3>
          <p className="diagnosis-modified-report-card__subtitle">
            {summary.subtitle}
          </p>
        </div>
        <div className="diagnosis-modified-report-card__meta">
          {summary.sessionLabel ? <span>{summary.sessionLabel}</span> : null}
          <span>{summary.updatedDateTimeLabel ?? "--"}</span>
        </div>
      </header>

      <div className="diagnosis-modified-report-card__grid">
        <div>
          <span>{"\u786e\u5b9a\u6027"}</span>
          <strong>{summary.certaintyLabel}</strong>
        </div>
        <div>
          <span>{"\u7f6e\u4fe1\u5ea6"}</span>
          <strong>
            {summary.confidenceRawLabel ?? summary.confidenceLabel}
          </strong>
        </div>
        <div>
          <span>{"\u4f18\u5148\u7ea7"}</span>
          <strong>{summary.priorityLabel ?? "--"}</strong>
        </div>
        <div>
          <span>{"\u66f4\u65b0\u65f6\u95f4"}</span>
          <strong>{summary.updatedTimeLabel ?? "--"}</strong>
        </div>
      </div>

      <div className="diagnosis-modified-report-card__sections">
        <section className="diagnosis-modified-report-card__section">
          <p className="diagnosis-modified-report-card__section-label">
            {"\u5f53\u524d\u7ed3\u8bba"}
          </p>
          <div className="diagnosis-modified-report-card__facts">
            <div className="diagnosis-modified-report-card__fact-row">
              <span className="diagnosis-modified-report-card__fact-label">
                {"\u6839\u56e0"}
              </span>
              <strong className="diagnosis-modified-report-card__fact-value">
                {summary.rootCause ?? primaryCandidate?.title ?? "--"}
              </strong>
            </div>
            <div className="diagnosis-modified-report-card__fact-row">
              <span className="diagnosis-modified-report-card__fact-label">
                {"\u5c42\u7ea7"}
              </span>
              <span className="diagnosis-modified-report-card__fact-value">
                {summary.rootCauseLayerLabel ??
                  summary.rootCauseLayer ??
                  primaryCandidate?.layer ??
                  "--"}
              </span>
            </div>
            <div className="diagnosis-modified-report-card__fact-row">
              <span className="diagnosis-modified-report-card__fact-label">
                {"\u5b9e\u4f53"}
              </span>
              <span className="diagnosis-modified-report-card__fact-value">
                {rootCauseEntities.length > 0
                  ? rootCauseEntities.join("\uFF0C")
                  : "--"}
              </span>
            </div>
            <div className="diagnosis-modified-report-card__fact-row">
              <span className="diagnosis-modified-report-card__fact-label">
                {"\u5f71\u54cd"}
              </span>
              <span className="diagnosis-modified-report-card__fact-value">
                {summary.impactSummary}
              </span>
            </div>
            <div className="diagnosis-modified-report-card__fact-row">
              <span className="diagnosis-modified-report-card__fact-label">
                {"\u53d7\u5f71\u54cd\u670d\u52a1"}
              </span>
              <span className="diagnosis-modified-report-card__fact-value">
                {summary.affectedServices.length > 0
                  ? summary.affectedServices.join("\uFF0C")
                  : "--"}
              </span>
            </div>
          </div>
        </section>

        <section className="diagnosis-modified-report-card__section">
          <p className="diagnosis-modified-report-card__section-label">
            {"\u5019\u9009\u6839\u56e0\uff08" + candidates.length + "\uff09"}
          </p>
          <div className="diagnosis-modified-report-card__candidates">
            {candidates.length > 0 ? (
              candidates.map((candidate, index) => (
                <article
                  key={candidate.id}
                  className="diagnosis-modified-report-card__candidate-item"
                >
                  <div className="diagnosis-modified-report-card__candidate-header">
                    <strong>
                      {"#" +
                        (candidate.rank ?? index + 1) +
                        " " +
                        candidate.title}
                    </strong>
                    <span>
                      {"\u7f6e\u4fe1\u5ea6 " + candidate.confidence.toFixed(2)}
                    </span>
                  </div>
                  <p className="diagnosis-modified-report-card__candidate-copy">
                    {"\u8bc1\u636e\u6458\u8981\uff1a" +
                      (candidate.evidenceSummary ?? candidate.summary)}
                  </p>
                  {candidate.distinguishingVerification ? (
                    <p className="diagnosis-modified-report-card__candidate-copy diagnosis-modified-report-card__candidate-copy--muted">
                      {"\u533a\u5206\u9a8c\u8bc1\uff1a" +
                        candidate.distinguishingVerification}
                    </p>
                  ) : null}
                </article>
              ))
            ) : (
              <p className="diagnosis-modified-report-card__section-copy">
                {"\u6682\u65e0\u5019\u9009\u6839\u56e0\u3002"}
              </p>
            )}
          </div>
        </section>

        <section className="diagnosis-modified-report-card__section">
          <p className="diagnosis-modified-report-card__section-label">
            {"\u5047\u8bbe\u4e0e\u8bc1\u636e"}
          </p>
          <div className="diagnosis-modified-report-card__hypotheses">
            {hypothesisRows.length > 0 ? (
              hypothesisRows.map((item, index) => (
                <article
                  key={item.id}
                  className="diagnosis-modified-report-card__hypothesis-item"
                >
                  <div className="diagnosis-modified-report-card__hypothesis-header">
                    <strong>
                      {String.fromCharCode(65 + index) +
                        ". " +
                        item.description}
                    </strong>
                    <ToneBadge tone={item.statusTone}>
                      {item.statusLabel}
                    </ToneBadge>
                  </div>
                  <p className="diagnosis-modified-report-card__section-copy">
                    {"\u652f\u6301\u8bc1\u636e " +
                      item.evidenceForCount +
                      " \u6761 | \u53cd\u8bc1 " +
                      item.evidenceAgainstCount +
                      " \u6761 | \u7f6e\u4fe1\u5ea6 " +
                      item.confidence.toFixed(2)}
                  </p>
                </article>
              ))
            ) : (
              <p className="diagnosis-modified-report-card__section-copy">
                {"\u6682\u65e0\u5047\u8bbe\u8bc1\u636e\u6570\u636e\u3002"}
              </p>
            )}
          </div>
        </section>

        <details className="diagnosis-modified-report-card__propagation">
          <summary className="diagnosis-modified-report-card__section-label">
            {"\u4f20\u64ad\u94fe\u8def\uff08\u6298\u53e0\uff09"}
          </summary>
          <div className="diagnosis-modified-report-card__propagation-body">
            {chainRows.length > 0 ? (
              chainRows.map((step) => (
                <article
                  key={step.id}
                  className="diagnosis-modified-report-card__propagation-item"
                >
                  <p>
                    {step.entityId +
                      " -> " +
                      step.metric +
                      " -> " +
                      step.valueBefore +
                      " -> " +
                      step.valueAfter +
                      " -> " +
                      step.description}
                  </p>
                </article>
              ))
            ) : (
              <p className="diagnosis-modified-report-card__section-copy">
                {"\u6682\u65e0\u4f20\u64ad\u94fe\u8def\u6570\u636e\u3002"}
              </p>
            )}
          </div>
        </details>
      </div>
    </section>
  );
}
function SystemEventBlock({
  item,
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "system" }>;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const hoverTime = formatTimestamp(item.timestamp);
  const categoryLabel =
    item.eventKind === "approval_result"
      ? "Approval Audit"
      : "Execution Progress";

  return (
    <article
      className="diagnosis-modified-process-row diagnosis-modified-process-row--system"
      title={hoverTime}
    >
      <div className="diagnosis-modified-process-row__body">
        <button
          className="diagnosis-modified-system-event__toggle"
          onClick={() => setIsExpanded((current) => !current)}
          type="button"
        >
          <span className="diagnosis-modified-thinking__icon" aria-hidden="true">
            <span
              className={cn(
                "diagnosis-modified-thinking__chevron",
                isExpanded && "diagnosis-modified-thinking__chevron--expanded",
              )}
            />
          </span>
          <span className="diagnosis-modified-system-event__label-wrap">
            <ToneBadge tone={item.statusTone}>{categoryLabel}</ToneBadge>
            <span className="diagnosis-modified-system-event__summary">
              {item.summary}
            </span>
          </span>
          <span className="diagnosis-modified-system-event__time">
            {hoverTime}
          </span>
        </button>

        {isExpanded ? (
          <div className="diagnosis-modified-system-event__panel">
            <div className="diagnosis-modified-system-event__content">
              {item.details.map((detail, index) => (
                <p key={`${item.id}-${index}`}>{detail}</p>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function ApprovalOverlay({
  open,
  plan,
  planVersion,
  rejectReason,
  canApprove,
  approvalBlockReason,
  isSubmitting,
  onRejectReasonChange,
  onApprove,
  onReject,
}: {
  open: boolean;
  plan?: DiagnosisModifiedPlanView;
  planVersion: number | null;
  rejectReason: string;
  canApprove: boolean;
  approvalBlockReason?: string;
  isSubmitting: boolean;
  onRejectReasonChange: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
}) {
  if (!open || !plan) {
    return null;
  }

  return (
    <section
      aria-live="polite"
      className="diagnosis-modified-approval-overlay"
      data-testid="diagnosis-approval-overlay"
    >
      <header className="diagnosis-modified-approval-overlay__header">
        <div>
          <div className="diagnosis-modified-approval-overlay__eyebrow">
            <ToneBadge tone="accent">{"\u5f85\u5ba1\u6279"}</ToneBadge>
            {planVersion ? (
              <ToneBadge tone="neutral">{`v${planVersion}`}</ToneBadge>
            ) : null}
            <ToneBadge tone="warning">{plan.priorityLabel}</ToneBadge>
            <ToneBadge tone="neutral">{plan.confidenceLabel}</ToneBadge>
          </div>
          <h3 className="diagnosis-modified-approval-overlay__title">
            {plan.title}
          </h3>
          <p className="diagnosis-modified-approval-overlay__description">
            {plan.description}
          </p>
        </div>
      </header>

      <div className="diagnosis-modified-approval-overlay__body">
        <div className="diagnosis-modified-approval-overlay__meta-row">
          {plan.safetyLabel ? (
            <ToneBadge tone="info">{plan.safetyLabel}</ToneBadge>
          ) : null}
          {plan.canaryLabel ? (
            <ToneBadge tone="neutral">{plan.canaryLabel}</ToneBadge>
          ) : null}
        </div>

        <ol className="diagnosis-modified-approval-overlay__actions">
          {plan.steps.map((step, index) => (
            <li key={step.id}>
              <span>{index + 1}</span>
              <div>
                <strong>{step.title}</strong>
                <p>{step.detail}</p>
                {step.paramsSummary ? <code>{step.paramsSummary}</code> : null}
              </div>
            </li>
          ))}
        </ol>

        <div className="diagnosis-modified-approval-overlay__reject">
          <label htmlFor="diagnosis-approval-reason">
            {"\u62d2\u7edd\u539f\u56e0\uff08\u62d2\u7edd\u65f6\u5fc5\u586b\uff09"}
          </label>
          <textarea
            data-testid="diagnosis-approval-reason"
            id="diagnosis-approval-reason"
            onChange={(event) => onRejectReasonChange(event.target.value)}
            placeholder={"\u8bf7\u8bf4\u660e\u4e3a\u4ec0\u4e48\u4e0d\u540c\u610f\u6267\u884c\u8be5\u4fee\u590d\u65b9\u6848"}
            rows={3}
            value={rejectReason}
          />
          {approvalBlockReason ? (
            <p className="diagnosis-modified-approval-overlay__note diagnosis-modified-approval-overlay__note--warning">
              {approvalBlockReason}
            </p>
          ) : (
            <p className="diagnosis-modified-approval-overlay__note">
              {"\u540c\u610f\u540e\u6d88\u606f\u6d41\u4f1a\u843d\u4e0b\u5ba1\u6279\u7ed3\u679c\uff0c\u5e76\u7ee7\u7eed\u8ffd\u52a0\u6267\u884c\u8fdb\u5ea6\u3002"}
            </p>
          )}
        </div>

        <div className="diagnosis-modified-approval-overlay__footer">
          <button
            className="diagnosis-modified-action-btn"
            data-testid="diagnosis-reject-button"
            disabled={isSubmitting || !canApprove || !rejectReason.trim()}
            onClick={onReject}
            type="button"
          >
            {isSubmitting ? "Submitting..." : "\u62d2\u7edd"}
          </button>
          <button
            className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary"
            data-testid="diagnosis-approve-button"
            disabled={isSubmitting || !canApprove}
            onClick={onApprove}
            type="button"
          >
            {isSubmitting ? "Submitting..." : "\u540c\u610f\u6267\u884c"}
          </button>
        </div>
      </div>
    </section>
  );
}

function DiagnosisModifiedPage() {
  const params = useParams<{ sessionId?: string }>();
  const routeSessionId = (params.sessionId ?? "").trim();
  const shouldBootstrapLiveSession = routeSessionId.length > 0;
  const [draft, setDraft] = useState("");
  const [demoTimeline, setDemoTimeline] = useState<
    DiagnosisModifiedTimelineItem[]
  >([]);
  const [demoCandidates, setDemoCandidates] = useState<
    DiagnosisModifiedCandidateView[]
  >([]);
  const [demoSummary, setDemoSummary] = useState<
    DiagnosisModifiedSummaryView | undefined
  >();
  const [demoPlan, setDemoPlan] = useState<
    DiagnosisModifiedPlanView | undefined
  >();
  const [demoHypotheses, setDemoHypotheses] = useState<
    DiagnosisModifiedHypothesisView[]
  >([]);
  const [demoPropagationChain, setDemoPropagationChain] = useState<
    DiagnosisModifiedPropagationStepView[]
  >([]);
  const [demoState, setDemoState] = useState<"idle" | "running" | "complete">(
    "idle",
  );
  const [lastDemoPrompt, setLastDemoPrompt] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [activeStreamingMessageId, setActiveStreamingMessageId] = useState<
    string | null
  >(null);
  const [liveTimeline, setLiveTimeline] = useState<
    DiagnosisModifiedTimelineItem[]
  >([]);

  const bottomRef = useRef<HTMLDivElement | null>(null);
  const demoTimerRef = useRef<number[]>([]);
  const demoRunTokenRef = useRef(0);

  const messageStreamResolversRef = useRef<Map<string, () => void>>(new Map());
  const messageStreamFallbackTimersRef = useRef<Map<string, number>>(new Map());

  const thinkingStreamResolversRef = useRef<Map<string, () => void>>(new Map());
  const thinkingStreamFallbackTimersRef = useRef<Map<string, number>>(
    new Map(),
  );
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
  const latestLiveSourceByIdRef = useRef<
    Map<string, DiagnosisModifiedTimelineItem>
  >(new Map());
  const initializedLiveSessionIdRef = useRef<string | null>(null);

  const {
    session,
    activeSessionId,
    messages,
    events,
    localAuditRecords,
    bootstrapStatus,
    traceStatus,
    isLoadingSession,
    isSendingMessage,
    connectionState,
    error,
    isApprovingPlan,
    approvalOverlayOpen,
    latestPlanVersion,
    canApprove,
    approvalBlockReason,
    bootstrapSession,
    sendMessage,
    approvePlan,
    applyEvent,
    setConnectionState,
  } = useDiagnosisStore();

  const liveView = useMemo(
    () => buildDiagnosisModifiedLiveView(session, messages, events, localAuditRecords),
    [events, localAuditRecords, messages, session],
  );
  const hasLiveSession =
    shouldBootstrapLiveSession &&
    bootstrapStatus === "ready" &&
    Boolean(session) &&
    Boolean(activeSessionId);

  useEffect(() => {
    if (!shouldBootstrapLiveSession) {
      return;
    }
    void bootstrapSession(routeSessionId);
  }, [bootstrapSession, routeSessionId, shouldBootstrapLiveSession]);

  useEffect(() => {
    setApprovalReason("");
  }, [activeSessionId, latestPlanVersion]);

  const handleApprovePlan = useCallback(async () => {
    try {
      await approvePlan({ approved: true });
      setApprovalReason("");
    } catch {
      // Store error state handles UI recovery.
    }
  }, [approvePlan]);

  const handleRejectPlan = useCallback(async () => {
    const reason = approvalReason.trim();
    if (!reason) {
      return;
    }

    try {
      await approvePlan({ approved: false, reason });
      setApprovalReason("");
    } catch {
      // Store error state handles UI recovery.
    }
  }, [approvalReason, approvePlan]);

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

  const waitForMessageStream = useCallback(
    (messageId: string, content: string) => {
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

          const fallbackTimerId =
            messageStreamFallbackTimersRef.current.get(messageId);
          if (fallbackTimerId) {
            window.clearTimeout(fallbackTimerId);
            messageStreamFallbackTimersRef.current.delete(messageId);
          }

          messageStreamResolversRef.current.delete(messageId);
          setActiveStreamingMessageId((current) =>
            current === messageId ? null : current,
          );
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
    },
    [],
  );

  const resolveThinkingStream = useCallback((thinkingId: string) => {
    const resolver = thinkingStreamResolversRef.current.get(thinkingId);
    resolver?.();
  }, []);

  const waitForThinkingStream = useCallback(
    (thinkingId: string, content: string) => {
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

          const fallbackTimerId =
            thinkingStreamFallbackTimersRef.current.get(thinkingId);
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
        thinkingStreamFallbackTimersRef.current.set(
          thinkingId,
          fallbackTimerId,
        );
      });
    },
    [],
  );

  const resolveLiveToolWaitersIfReady = useCallback(
    (timelineItems: DiagnosisModifiedTimelineItem[]) => {
      for (const [toolId, resolver] of [
        ...liveToolWaiterResolversRef.current.entries(),
      ]) {
        const toolItem = timelineItems.find(
          (
            item,
          ): item is Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> =>
            item.kind === "tool" && item.id === toolId,
        );
        if (toolItem && toolItem.status !== "loading") {
          resolver();
        }
      }
    },
    [],
  );

  useEffect(() => {
    liveTimelineRef.current = liveTimeline;
    resolveLiveToolWaitersIfReady(liveTimeline);
  }, [liveTimeline, resolveLiveToolWaitersIfReady]);

  const waitForLiveToolTerminal = useCallback((toolId: string) => {
    return new Promise<void>((resolve) => {
      const existingTool = liveTimelineRef.current.find(
        (
          item,
        ): item is Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> =>
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
              if (
                item.kind !== "tool" ||
                item.id !== toolId ||
                item.status !== "loading"
              ) {
                return item;
              }

              const timeoutSummary =
                "Timed out after 15s waiting for tool_result.";
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
      const timeoutTimerId = window.setTimeout(
        () => settle(true),
        TOOL_RESULT_TIMEOUT_MS,
      );
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
      while (
        liveQueuedItemsRef.current.length > 0 &&
        queueToken === liveQueueTokenRef.current
      ) {
        const queuedItem = liveQueuedItemsRef.current.shift();
        if (!queuedItem) {
          continue;
        }

        liveQueuedIdsRef.current.delete(queuedItem.id);
        const nextItem =
          latestLiveSourceByIdRef.current.get(queuedItem.id) ?? queuedItem;

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

    const currentSessionId =
      activeSessionId ?? session?.session_id ?? routeSessionId;
    const sourceTimeline = liveView.timeline;
    const sourceById = new Map(sourceTimeline.map((item) => [item.id, item]));
    latestLiveSourceByIdRef.current = sourceById;

    if (initializedLiveSessionIdRef.current !== currentSessionId) {
      initializedLiveSessionIdRef.current = currentSessionId;
      liveQueueTokenRef.current += 1;
      liveQueuedItemsRef.current = [];
      liveQueuedIdsRef.current = new Set();
      liveDisplayedIdsRef.current = new Set(
        sourceTimeline.map((item) => item.id),
      );
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
      setDemoHypotheses([]);
      setDemoPropagationChain([]);
      setDemoSummary(undefined);
      setDemoPlan(undefined);
      setDemoState("running");
      setLastDemoPrompt(normalizedPrompt);
      setDraft("");

      void (async () => {
        let previousDelay = 0;
        const activeDemoThinkingIds = new Set<string>();
        const activeDemoToolIds = new Set<string>();
        const knownDemoThinkingIds = new Set<string>();
        const knownDemoToolIds = new Set<string>();
        const deferredDemoEvents: DiagnosisModifiedDemoEvent[] = [];

        const canProcessDemoEvent = (event: DiagnosisModifiedDemoEvent) => {
          if (activeDemoThinkingIds.size > 0) {
            return (
              event.type === "update_thinking" &&
              activeDemoThinkingIds.has(event.targetId)
            );
          }

          if (activeDemoToolIds.size > 0) {
            return (
              event.type === "update_tool" &&
              activeDemoToolIds.has(event.targetId)
            );
          }

          if (event.type === "update_thinking") {
            return knownDemoThinkingIds.has(event.targetId);
          }

          if (event.type === "update_tool") {
            return knownDemoToolIds.has(event.targetId);
          }

          return true;
        };

        const applyDemoEvent = async (
          event: DiagnosisModifiedDemoEvent,
        ): Promise<boolean> => {
          if (event.type === "append") {
            if (
              event.item.kind === "thinking" &&
              event.item.status === "thinking"
            ) {
              knownDemoThinkingIds.add(event.item.id);
              activeDemoThinkingIds.add(event.item.id);
              demoThinkingStartedAtRef.current.set(event.item.id, Date.now());
              setDemoTimeline((current) => [...current, event.item]);

              await waitForThinkingStream(event.item.id, event.item.content);

              const startedAt = demoThinkingStartedAtRef.current.get(
                event.item.id,
              );
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

            if (
              event.item.kind === "message" &&
              event.item.role === "assistant"
            ) {
              const { thinking, conclusion } = splitThinkingAndConclusion(
                event.item.content,
              );

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

                const startedAt =
                  demoThinkingStartedAtRef.current.get(thoughtId);
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
                    ? {
                        ...event.item,
                        id: `${event.item.id}-answer`,
                        content: conclusion,
                      }
                    : { ...event.item, content: conclusion };

                setDemoTimeline((current) => [...current, conclusionItem]);
                await waitForMessageStream(
                  conclusionItem.id,
                  conclusionItem.content,
                );
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
                demoToolLoadingStartedAtRef.current.set(
                  event.item.id,
                  Date.now(),
                );
              } else {
                activeDemoToolIds.delete(event.item.id);
                demoToolLoadingStartedAtRef.current.delete(event.item.id);
              }
            }
            return true;
          }

          if (event.type === "update_tool") {
            if (!knownDemoToolIds.has(event.targetId)) {
              return false;
            }

            const toolLoadingStartedAt =
              demoToolLoadingStartedAtRef.current.get(event.targetId);
            if (typeof toolLoadingStartedAt === "number") {
              const elapsedMs = Date.now() - toolLoadingStartedAt;
              const remainingMs = Math.max(
                0,
                DEMO_MIN_TOOL_LOADING_DWELL_MS - elapsedMs,
              );
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

            const startedAt = demoThinkingStartedAtRef.current.get(
              event.targetId,
            );
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
                      ? (event.thoughtDurationSec ??
                        elapsedSec ??
                        item.thoughtDurationSec ??
                        estimateThoughtDurationSecFromContent(item.content))
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
          setDemoHypotheses(scenario.hypotheses ?? []);
          setDemoPropagationChain(scenario.propagationChain ?? []);
          setDemoSummary(scenario.summary);
          setDemoPlan(scenario.plan);
          clearDemoToolLoadingStates();
          setDemoState("complete");
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
          await waitForDemoDelay(
            Math.round(deltaMs * DEMO_EVENT_SLOWDOWN),
            runToken,
          );

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
    [
      clearDemoTimers,
      clearDemoToolLoadingStates,
      clearPendingMessageStreams,
      clearPendingThinkingStreams,
      waitForDemoDelay,
      waitForMessageStream,
      waitForThinkingStream,
    ],
  );

  const handleSubmit = useCallback(() => {
    const content = draft.trim();
    if (!content) {
      return;
    }

    if (hasLiveSession && activeSessionId) {
      setDraft("");
      void sendMessage(content);
      return;
    }

    startDemo(content);
  }, [activeSessionId, draft, hasLiveSession, sendMessage, startDemo]);

  const handleInputKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  const handleRealtimeEvent = useCallback(
    (event: Parameters<typeof applyEvent>[0]) => {
      applyEvent(event);
    },
    [applyEvent],
  );

  const websocketEnabled =
    shouldBootstrapLiveSession &&
    import.meta.env.VITE_WS_ENABLED === "true" &&
    Boolean(activeSessionId);
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
  const activeCandidates = hasLiveSession
    ? liveView.candidates
    : demoCandidates;
  const activeHypotheses = hasLiveSession
    ? (liveView.hypotheses ?? [])
    : demoHypotheses;
  const activePropagationChain = hasLiveSession
    ? (liveView.propagationChain ?? [])
    : demoPropagationChain;
  const activeSummary = hasLiveSession ? liveView.summary : demoSummary;
  const activePlan = hasLiveSession ? liveView.plan : demoPlan;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activePlan, activeSummary, activeTimeline]);

  const inlineError = useMemo(
    () => (shouldBootstrapLiveSession ? resolveInlineError(error) : null),
    [error, shouldBootstrapLiveSession],
  );

  const composerDisabled =
    (shouldBootstrapLiveSession ? isLoadingSession : false) ||
    isSendingMessage ||
    demoState === "running";
  const introCopy = hasLiveSession
    ? "\u5f53\u524d\u9875\u9762\u6b63\u5728\u6d88\u8d39\u771f\u5b9e\u8bca\u65ad\u4f1a\u8bdd\uff0c\u5e76\u6309\u7edf\u4e00\u6d41\u7a0b\u5448\u73b0\u601d\u8003\u3001\u5de5\u5177\u8c03\u7528\u3001\u6839\u56e0\u5206\u6790\u4e0e\u5ba1\u6279\u6267\u884c\u3002"
    : demoState === "idle"
      ? "\u8f93\u5165\u8bca\u65ad\u95ee\u9898\u540e\uff0c\u9875\u9762\u4f1a\u6309\u8f83\u6162\u8282\u594f\u56de\u653e\u5b8c\u6574\u8bca\u65ad\u8fc7\u7a0b\uff0c\u65b9\u4fbf\u9010\u6b65\u67e5\u770b\u6bcf\u4e00\u6b21\u601d\u8003\u4e0e\u5de5\u5177\u8c03\u7528\u3002"
      : "\u5f53\u524d\u6b63\u5728\u6309\u6162\u901f\u56de\u653e\u8bca\u65ad\u6d41\u7a0b\u3002";

  return (
    <div className="page-grid diagnosis-modified-page">
      <section className="page-stage diagnosis-modified-stage">
        <div className="page-stage__panel diagnosis-modified-shell">
          <div className="diagnosis-modified-shell__statusbar">
            <div className="diagnosis-modified-shell__badges">
              {hasLiveSession && session ? (
                <>
                  <ToneBadge tone="accent">{session.alert.alert_name}</ToneBadge>
                  <ToneBadge tone={getDiagnosisStatusBadgeTone(session.status)}>
                    {formatWorkflowStatus(session.status)}
                  </ToneBadge>
                  <ToneBadge
                    tone={getSeverityBadgeTone(session.alert.severity)}
                  >
                    {formatSeverity(session.alert.severity)}
                  </ToneBadge>
                </>
              ) : (
                <>
                  <ToneBadge tone={hasLiveSession ? "success" : "accent"}>
                    {hasLiveSession
                      ? "\u5b9e\u65f6\u4f1a\u8bdd"
                      : "\u6f14\u793a\u6a21\u5f0f"}
                  </ToneBadge>
                  {hasLiveSession && activeSessionId ? (
                    <ToneBadge tone="neutral">{activeSessionId}</ToneBadge>
                  ) : null}
                  {hasLiveSession ? (
                    <ToneBadge
                      tone={connectionState === "open" ? "success" : "warning"}
                    >{`\u5b9e\u65f6\u94fe\u8def ${connectionState}`}</ToneBadge>
                  ) : null}
                </>
              )}
            </div>
          </div>

          <div className="diagnosis-modified-shell__body">
            <div className="diagnosis-modified-feed">
              {activeTimeline.length === 0 ? (
                <div className="diagnosis-modified-empty-state">
                  <div className="diagnosis-modified-empty-state__icon">
                    <AppIcon name="aiChat" size={18} />
                  </div>
                  <h2>Welcome to the RCA Agent</h2>
                  <p>
                    Type your request below to trigger the ReAct diagnostic
                    process, or use the pre-filled example.
                  </p>
                </div>
              ) : (
                activeTimeline.map((item) => {
                  if (item.kind === "message") {
                    const shouldAnimateAssistantMessage =
                      item.role === "assistant" &&
                      activeStreamingMessageId === item.id;

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
                    const shouldAnimateThinking =
                      !hasLiveSession && item.status === "thinking";
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

                  if (item.kind === "system") {
                    return <SystemEventBlock item={item} key={item.id} />;
                  }

                  return <ToolCard item={item} key={item.id} />;
                })
              )}

              {hasLiveSession && traceStatus === "empty" ? (
                <div className="diagnosis-modified-inline-note">
                  The live session has not produced trace entries yet. The input
                  remains available while waiting for incremental diagnosis
                  events.
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

              {activeSummary ? (
                <RCAReportCard
                  candidates={activeCandidates}
                  hypotheses={activeHypotheses}
                  propagationChain={activePropagationChain}
                  summary={activeSummary}
                />
              ) : null}
              <div ref={bottomRef} />
            </div>
          </div>

          <footer className="diagnosis-modified-shell__footer">
            <div className="diagnosis-modified-composer-anchor">
              <ApprovalOverlay
                approvalBlockReason={approvalBlockReason}
                canApprove={canApprove}
                isSubmitting={isApprovingPlan}
                onApprove={() => void handleApprovePlan()}
                onReject={() => void handleRejectPlan()}
                onRejectReasonChange={setApprovalReason}
                open={hasLiveSession && approvalOverlayOpen}
                plan={activePlan}
                planVersion={latestPlanVersion}
                rejectReason={approvalReason}
              />
              <div className="diagnosis-modified-composer">
                <textarea
                  className="diagnosis-modified-composer__input"
                  disabled={composerDisabled}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={handleInputKeyDown}
                  placeholder={
                    hasLiveSession
                      ? "Continue the current diagnosis session, for example: explain why these root-cause candidates were selected."
                      : "Ask the agent to diagnose an issue... (Press Enter to start)"
                  }
                  rows={1}
                  value={draft}
                />
                <div className="diagnosis-modified-composer__actions">
                  <p className="diagnosis-modified-composer__hint">
                    {hasLiveSession
                      ? "The live data stream is preserved and rendered with Toolcall pacing and hierarchy."
                      : "Without an active session, local demo mode runs and replays a slower Toolcall-style diagnosis flow."}
                  </p>
                  <div className="diagnosis-modified-composer__buttons">
                    {!hasLiveSession && demoTimeline.length > 0 ? (
                      <button
                        className="diagnosis-modified-send-btn diagnosis-modified-send-btn--secondary"
                        onClick={() =>
                          startDemo(
                            lastDemoPrompt ||
                              draft ||
                              "Analyze auth-svc latency and error-rate spike in the past hour",
                          )
                        }
                        type="button"
                      >
                        Replay
                      </button>
                    ) : null}
                    <button
                      className="diagnosis-modified-send-btn"
                      disabled={composerDisabled || !draft.trim()}
                      onClick={handleSubmit}
                      type="button"
                    >
                      <AppIcon name="send" size={14} />
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <p className="diagnosis-modified-shell__footer-note">
              Agent can make mistakes. Consider verifying important information.
            </p>
          </footer>
        </div>
      </section>
    </div>
  );
}
export default DiagnosisModifiedPage;
