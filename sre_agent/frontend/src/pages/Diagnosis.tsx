import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type UIEvent,
} from "react";
import { useParams } from "react-router-dom";

import { buildBackendWsUrl } from "../api/ws";
import { AppIcon } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";
import { formatSeverity, formatWorkflowStatus } from "../utils/display";
import { formatTimestamp } from "../utils/format";
import {
  buildDiagnosisDemoScenario,
  buildDiagnosisLiveView,
  groupExecutionRunTimeline,
  normalizeDiagnosisDisplayText,
  type DiagnosisCandidateView,
  type DiagnosisDemoEvent,
  type DiagnosisPlanView,
  type DiagnosisHypothesisView,
  type DiagnosisPropagationStepView,
  type DiagnosisSummaryView,
  type DiagnosisTimelineItem,
} from "./diagnosisModel";

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
const DEMO_APPROVAL_CARD_DELAY_MS = 320;
const DEMO_APPROVAL_SUBMIT_DELAY_MS = 720;
const TOOL_RESULT_TIMEOUT_MS = 15_000;
const STREAM_COMPLETION_BUFFER_MS = 640;
const AUTO_SCROLL_BOTTOM_THRESHOLD_PX = 48;
const SYSTEM_EVENT_PREFIX_PATTERN = /^\[(?:system|\u7cfb\u7edf)\]\s*/i;
const APPROVAL_KEY_DETAIL_PATTERNS = [
  /^\u5ba1\u6279\u53cd\u9988\s*[:\uff1a]/i,
  /^\u6267\u884c\u8fb9\u754c\s*[:\uff1a]/i,
  /^\u5ba1\u6279\u52a8\u4f5c\s*[:\uff1a]/i,
  /^\u62d2\u7edd\u539f\u56e0\s*[:\uff1a]/i,
  /^\u65b9\u6848(?:\u6807\u9898|\u7248\u672c|\s*ID)\s*[:\uff1a]/i,
  /^approval feedback\s*[:\uff1a]/i,
  /^execution boundary\s*[:\uff1a]/i,
  /^approval action\s*[:\uff1a]/i,
  /^reject reason\s*[:\uff1a]/i,
  /^plan (?:title|version|id)\s*[:\uff1a]/i,
];

const EXECUTION_DETAIL_NOISE_PATTERNS = [
  /^\u72b6\u6001\u65f6\u95f4\s*[:\uff1a]/i,
  /^\u6267\u884c\u9636\u6bb5\s*[:\uff1a]/i,
  /^\u6267\u884c\u4eba\s*[:\uff1a]/i,
  /^\u8c03\u7528\s*skill\s*[:\uff1a]/i,
  /^status time\s*[:\uff1a]/i,
  /^stage\s*[:\uff1a]/i,
  /^operator\s*[:\uff1a]/i,
  /^skill\s*[:\uff1a]/i,
];

const EXECUTION_DETAIL_SIGNAL_PATTERNS = [
  /\u6307\u6807|\u53cd\u9988|\u89c2\u5bdf|\u7ed3\u8bba|\u544a\u8b66|\u6062\u590d|\u56de\u6eda|\u4e0b\u4e00\u6b65/i,
  /p95|p99|latency|error|gpu|util|queue|throughput/i,
];

type DemoApprovalDecision = "approved" | "rejected";
type ApprovalSurfaceResolution = {
  label: string;
  tone: BadgeTone;
  description: string;
};

type ApprovalSurfaceView = {
  statusLabel: string;
  statusTone: BadgeTone;
  title: string;
  description: string;
  impactLabel: string;
  impactSummary: string;
  metaItems: string[];
  steps: DiagnosisPlanView["steps"];
  hasTechnicalDetails: boolean;
};

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

function stripSystemEventPrefix(summary: string) {
  return summary.replace(SYSTEM_EVENT_PREFIX_PATTERN, "").trim();
}

function dedupeNonEmptyLines(lines: string[]) {
  const seen = new Set<string>();
  const deduped: string[] = [];

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed || seen.has(trimmed)) {
      return;
    }

    seen.add(trimmed);
    deduped.push(trimmed);
  });

  return deduped;
}

function extractApprovalApproverLine(values: string[]): string | null {
  for (const value of values) {
    const trimmed = value.trim();
    if (!trimmed) {
      continue;
    }

    let matched =
      /^(\u5ba1\u6279\u4eba)\s*[:\uff1a]\s*(.+)$/i.exec(trimmed) ??
      /(\u5ba1\u6279\u4eba)\s+([^\s,\uff0c)\uff09]+)/i.exec(trimmed);
    if (matched) {
      return `${matched[1]}\uff1a${matched[2].trim()}`;
    }

    matched =
      /^(approver)\s*[:\uff1a]\s*(.+)$/i.exec(trimmed) ??
      /(approver)\s+([^\s,\uff0c)\uff09]+)/i.exec(trimmed);
    if (matched) {
      return `Approver: ${matched[2].trim()}`;
    }
  }

  return null;
}

function extractApprovalResultDetailLines(
  item: Extract<DiagnosisTimelineItem, { kind: "system" }>,
) {
  const summaryLine = stripSystemEventPrefix(item.summary);
  const approverLine = extractApprovalApproverLine([summaryLine, ...item.details]);
  const keyLines = item.details.filter((detail) =>
    APPROVAL_KEY_DETAIL_PATTERNS.some((pattern) => pattern.test(detail.trim())),
  );

  const detailLines = dedupeNonEmptyLines([
    ...(approverLine ? [approverLine] : []),
    ...keyLines,
  ]);

  if (detailLines.length > 0) {
    return detailLines;
  }

  return dedupeNonEmptyLines([summaryLine]);
}

function compactExecutionDetailLines(lines: string[]) {
  const filtered = dedupeNonEmptyLines(lines)
    .filter((line) => !EXECUTION_DETAIL_NOISE_PATTERNS.some((pattern) => pattern.test(line.trim())))
    .filter((line) => EXECUTION_DETAIL_SIGNAL_PATTERNS.some((pattern) => pattern.test(line)));

  if (filtered.length > 0) {
    return filtered.slice(0, 3);
  }

  return dedupeNonEmptyLines(lines)
    .filter((line) => !EXECUTION_DETAIL_NOISE_PATTERNS.some((pattern) => pattern.test(line.trim())))
    .slice(0, 2);
}

function formatRunStatusLabel(status: "running" | "success" | "error" | "timeout" | "loading") {
  switch (status) {
    case "running":
    case "loading":
      return "running";
    case "success":
      return "done";
    case "error":
      return "error";
    case "timeout":
      return "timeout";
    default:
      return status;
  }
}

function getRunPhaseLabel(phase: Extract<DiagnosisTimelineItem, { kind: "run" }>['phase']) {
  if (phase === "canary") {
    return "canary stage";
  }
  if (phase === "full") {
    return "full rollout";
  }
  return "execution";
}

function extractSystemEventDetailLines(
  item: Extract<DiagnosisTimelineItem, { kind: "system" }>,
) {
  const usefulLines = compactExecutionDetailLines(item.details);
  if (usefulLines.length > 0) {
    return usefulLines;
  }

  const summaryLine = stripSystemEventPrefix(item.summary);
  return summaryLine ? [summaryLine] : [];
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
        "diagnosis-workspace-badge",
        `diagnosis-workspace-badge--${tone}`,
      )}
    >
      {children}
    </span>
  );
}

function getApprovalConfidenceMeta(label?: string) {
  if (!label) {
    return null;
  }
  return /\u7f6e\u4fe1/u.test(label) ? label : `${label} \u7f6e\u4fe1`;
}

function getApprovalStepDetailLines(step: DiagnosisPlanView["steps"][number]) {
  const lines: string[] = [];
  const detail = step.detail?.trim();
  const paramsSummary = step.paramsSummary?.trim();

  if (detail && detail !== step.title.trim()) {
    lines.push(detail);
  }

  if (paramsSummary) {
    lines.push(paramsSummary);
  }

  return lines;
}

function buildApprovalSurfaceView({
  plan,
  summary,
  planVersion,
  statusLabel,
  statusTone,
}: {
  plan?: DiagnosisPlanView;
  summary?: DiagnosisSummaryView;
  planVersion: number | null;
  statusLabel: string;
  statusTone: BadgeTone;
}): ApprovalSurfaceView | null {
  if (!plan) {
    return null;
  }

  const impactSummary =
    summary?.impactSummary?.trim() ||
    plan.safetyLabel?.trim() ||
    plan.canaryLabel?.trim() ||
    plan.description.trim();
  const metaItems = [
    plan.safetyLabel?.trim(),
    plan.priorityLabel?.trim(),
    getApprovalConfidenceMeta(plan.confidenceLabel),
    planVersion ? `v${planVersion}` : null,
    plan.canaryLabel?.trim(),
  ].filter((value): value is string => Boolean(value && value.trim().length > 0));
  const dedupedMetaItems = metaItems.filter(
    (value, index, items) => items.indexOf(value) === index,
  );

  return {
    statusLabel,
    statusTone,
    title: plan.title,
    description: plan.description,
    impactLabel: summary?.impactSummary?.trim() ? "\u98ce\u9669\u5f71\u54cd" : "\u6267\u884c\u8fb9\u754c",
    impactSummary,
    metaItems: dedupedMetaItems,
    steps: plan.steps,
    hasTechnicalDetails: plan.steps.some(
      (step) => getApprovalStepDetailLines(step).length > 0,
    ),
  };
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
        <span className="diagnosis-workspace-caret" aria-hidden="true" />
      ) : null}
    </span>
  );
}

function MessageRow({
  item,
  animate,
  onStreamComplete,
}: {
  item: Extract<DiagnosisTimelineItem, { kind: "message" }>;
  animate: boolean;
  onStreamComplete?: () => void;
}) {
  const isUser = item.role === "user";
  const hoverTime = formatTimestamp(item.timestamp);

  return (
    <article
      className={cn(
        "diagnosis-workspace-message-row",
        isUser && "diagnosis-workspace-message-row--user",
      )}
      title={hoverTime}
    >
      <div className="diagnosis-workspace-message-row__body">
        <span
          className="diagnosis-workspace-message-row__hover-time"
          aria-hidden="true"
        >
          {hoverTime}
        </span>
        <p className="diagnosis-workspace-message-row__text">
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
  item: Extract<DiagnosisTimelineItem, { kind: "thinking" }>;
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
    <article className="diagnosis-workspace-process-row">
      <div className="diagnosis-workspace-process-row__body">
        <button
          className={cn(
            "diagnosis-workspace-thinking__toggle",
            !isThinking && "diagnosis-workspace-thinking__toggle--interactive",
          )}
          onClick={() => {
            if (!isThinking) {
              setIsExpanded((current) => !current);
            }
          }}
          type="button"
        >
          <span
            className="diagnosis-workspace-thinking__icon"
            aria-hidden="true"
          >
            {isThinking ? (
              <span className="diagnosis-workspace-thinking__pulse" />
            ) : (
              <span
                className={cn(
                  "diagnosis-workspace-thinking__chevron",
                  isExpanded &&
                    "diagnosis-workspace-thinking__chevron--expanded",
                )}
              />
            )}
          </span>
          <span className="diagnosis-workspace-thinking__label-wrap">
            {isThinking ? (
              <span
                className="diagnosis-workspace-thinking__label-tag"
                aria-hidden="true"
              >
                <AppIcon name="spark" size={11} />
              </span>
            ) : null}
            <span
              className={cn(
                "diagnosis-workspace-thinking__label",
                isThinking && "diagnosis-workspace-thinking__label--thinking",
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
            <span className="diagnosis-workspace-thinking__tool">
              {item.toolName}
            </span>
          ) : null}
        </button>

        {isExpanded ? (
          <div className="diagnosis-workspace-thinking__panel">
            <div className="diagnosis-workspace-thinking__content">
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
  item: Extract<DiagnosisTimelineItem, { kind: "tool" }>;
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
    <article className="diagnosis-workspace-process-row diagnosis-workspace-process-row--tool">
      <div className="diagnosis-workspace-process-row__body">
        <button
          className={cn(
            "diagnosis-workspace-tool-card",
            `diagnosis-workspace-tool-card--${item.status}`,
            hasDetails &&
              item.status !== "loading" &&
              "diagnosis-workspace-tool-card--interactive",
          )}
          onClick={() => {
            if (hasDetails && item.status !== "loading") {
              setIsExpanded((current) => !current);
            }
          }}
          type="button"
        >
          <div className="diagnosis-workspace-tool-card__header">
            <div
              className="diagnosis-workspace-tool-card__status-icon"
              aria-hidden="true"
            >
              <span
                className={cn(
                  "diagnosis-workspace-tool-card__status-indicator",
                  `diagnosis-workspace-tool-card__status-indicator--${item.status}`,
                )}
              />
            </div>
            <div className="diagnosis-workspace-tool-card__header-copy">
              <strong>{item.toolName}</strong>
              {Object.keys(item.params).length > 0 ? (
                <span>{JSON.stringify(item.params)}</span>
              ) : null}
            </div>
            <span className="diagnosis-workspace-tool-card__status-label">
              {item.status}
            </span>
          </div>

          {isExpanded ? (
            <div className="diagnosis-workspace-tool-card__body">
              {item.status === "loading" ? (
                <div className="diagnosis-workspace-tool-card__loading">
                  <div>
                    <span className="diagnosis-workspace-tool-card__loading-indicator" />
                    <span>Connecting to telemetry stream...</span>
                  </div>
                  <div>
                    <span className="diagnosis-workspace-tool-card__loading-indicator" />
                    <span>Querying diagnostics context...</span>
                  </div>
                </div>
              ) : (
                <div className="diagnosis-workspace-tool-card__details">
                  {Object.keys(item.params).length > 0 ? (
                    <pre className="diagnosis-workspace-tool-card__params">
                      {JSON.stringify(item.params, null, 2)}
                    </pre>
                  ) : null}
                  {item.summaryLines.length > 0 ? (
                    <div className="diagnosis-workspace-tool-card__results">
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

function ExecutionRunBlock({
  item,
}: {
  item: Extract<DiagnosisTimelineItem, { kind: "run" }>;
}) {
  const activeStep =
    [...item.steps].reverse().find((step) => step.status === "running") ??
    item.steps[item.steps.length - 1];
  const [expandedStepIds, setExpandedStepIds] = useState<Set<string>>(
    () => new Set(activeStep ? [activeStep.id] : []),
  );
  const [expandedToolIds, setExpandedToolIds] = useState<Set<string>>(
    () => new Set(),
  );

  useEffect(() => {
    if (!activeStep) {
      return;
    }
    setExpandedStepIds((current) => {
      if (current.has(activeStep.id)) {
        return current;
      }
      const next = new Set(current);
      next.add(activeStep.id);
      return next;
    });
  }, [activeStep?.id]);

  const toolsByStep = useMemo(() => {
    const grouped = new Map<string, typeof item.tools>();
    item.tools.forEach((tool) => {
      const key = tool.stepId ?? "__run";
      const current = grouped.get(key) ?? [];
      current.push(tool);
      grouped.set(key, current);
    });
    return grouped;
  }, [item.tools]);

  const toggleStep = (stepId: string) => {
    setExpandedStepIds((current) => {
      const next = new Set(current);
      if (next.has(stepId)) {
        next.delete(stepId);
      } else {
        next.add(stepId);
      }
      return next;
    });
  };

  const toggleTool = (toolId: string) => {
    setExpandedToolIds((current) => {
      const next = new Set(current);
      if (next.has(toolId)) {
        next.delete(toolId);
      } else {
        next.add(toolId);
      }
      return next;
    });
  };

  const phaseLabel = getRunPhaseLabel(item.phase);
  const compactMetrics = item.metrics.slice(0, 3);

  return (
    <section
      className={cn(
        "diagnosis-workspace-run-block",
        `diagnosis-workspace-run-block--${item.status}`,
        `diagnosis-workspace-run-block--phase-${item.phase}`,
      )}
      data-testid="diagnosis-execution-run-block"
      title={`${formatTimestamp(item.startedAt)} - ${formatTimestamp(item.updatedAt)}`}
    >
      <header className="diagnosis-workspace-run-block__header">
        <div className="diagnosis-workspace-run-block__heading">
          <span className="diagnosis-workspace-run-block__eyebrow">
            {phaseLabel}
          </span>
          <div className="diagnosis-workspace-run-block__title-row">
            <h3>{item.title}</h3>
            <ToneBadge tone="neutral">{item.currentStageLabel}</ToneBadge>
          </div>
        </div>
        <div className="diagnosis-workspace-run-block__meta">
          <span>{`updated ${formatTimestamp(item.updatedAt)}`}</span>
        </div>
      </header>

      <div className="diagnosis-workspace-run-block__progress">
        <div className="diagnosis-workspace-run-block__progress-row">
          <span>{item.progress.label}</span>
          <strong>{item.progress.value}%</strong>
        </div>
        <div className="progress-track remediation-progress-track remediation-progress-track--canary">
          <div
            className="progress-track__fill remediation-progress-track__fill remediation-progress-track__fill--canary"
            style={{ width: `${item.progress.value}%` }}
          />
        </div>
        {item.progress.helper ? <p>{item.progress.helper}</p> : null}
      </div>

      {compactMetrics.length > 0 ? (
        <div className="diagnosis-workspace-run-block__metrics">
          {compactMetrics.map((metric) => (
            <span key={metric}>{metric}</span>
          ))}
        </div>
      ) : null}

      <div className="diagnosis-workspace-run-block__steps">
        {item.steps.map((step, index) => {
          const isExpanded = expandedStepIds.has(step.id);
          const stepTools = toolsByStep.get(step.id) ?? [];
          const stepDetails = compactExecutionDetailLines(step.details);
          return (
            <article
              className={cn(
                "diagnosis-workspace-run-step",
                `diagnosis-workspace-run-step--${step.status}`,
              )}
              key={step.id}
            >
              <div className="diagnosis-workspace-run-step__rail" aria-hidden="true">
                <span className="diagnosis-workspace-run-step__dot" />
                {index < item.steps.length - 1 ? (
                  <span className="diagnosis-workspace-run-step__line" />
                ) : null}
              </div>
              <div className="diagnosis-workspace-run-step__body">
                <button
                  aria-expanded={isExpanded}
                  className="diagnosis-workspace-run-step__toggle"
                  onClick={() => toggleStep(step.id)}
                  type="button"
                >
                  <span className="diagnosis-workspace-run-step__title-wrap">
                    <strong>{step.title}</strong>
                    <span>{step.summary}</span>
                  </span>
                  <span className="diagnosis-workspace-run-step__state">
                    {step.progress ? `${step.progress.value}%` : formatRunStatusLabel(step.status)}
                  </span>
                </button>

                {isExpanded ? (
                  <div className="diagnosis-workspace-run-step__panel">
                    {stepDetails.length > 0 ? (
                      <div className="diagnosis-workspace-run-step__details">
                        {stepDetails.map((detail) => (
                          <p key={`${step.id}-${detail}`}>{detail}</p>
                        ))}
                      </div>
                    ) : null}

                    {step.metricLines.length > 0 ? (
                      <div className="diagnosis-workspace-run-step__metric-row">
                        {step.metricLines.slice(0, 3).map((metric) => (
                          <span key={`${step.id}-${metric}`}>{metric}</span>
                        ))}
                      </div>
                    ) : null}

                    {stepTools.length > 0 ? (
                      <div className="diagnosis-workspace-run-tools">
                        {stepTools.map((tool) => {
                          const toolExpanded = expandedToolIds.has(tool.id);
                          const toolLines = dedupeNonEmptyLines(tool.summaryLines);
                          const previewLine = toolLines[0] ?? "";
                          const detailLines = toolLines.slice(1, 4);
                          const hasToolDetails = detailLines.length > 0;
                          return (
                            <div
                              className={cn(
                                "diagnosis-workspace-run-tool",
                                `diagnosis-workspace-run-tool--${tool.status}`,
                              )}
                              key={tool.id}
                            >
                              <button
                                aria-expanded={toolExpanded}
                                className="diagnosis-workspace-run-tool__toggle"
                                disabled={!hasToolDetails}
                                onClick={() => toggleTool(tool.id)}
                                type="button"
                              >
                                <span
                                  className={cn(
                                    "diagnosis-workspace-tool-card__status-indicator",
                                    `diagnosis-workspace-tool-card__status-indicator--${tool.status}`,
                                  )}
                                  aria-hidden="true"
                                />
                                <span className="diagnosis-workspace-run-tool__copy">
                                  <strong>{tool.toolName}</strong>
                                  {previewLine ? <span>{previewLine}</span> : null}
                                </span>
                                <span className="diagnosis-workspace-run-tool__status">
                                  {formatRunStatusLabel(tool.status)}
                                </span>
                              </button>
                              {toolExpanded ? (
                                <div className="diagnosis-workspace-run-tool__details">
                                  {detailLines.map((line) => (
                                    <p key={`${tool.id}-${line}`}>{line}</p>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function RCAReportCard({
  item,
}: {
  item: Extract<DiagnosisTimelineItem, { kind: "report" }>;
}) {
  const {
    summary,
    candidates,
    hypotheses,
    propagationChain,
    planStatusLabel,
    planStatusTone,
  } = item;
  const primaryCandidate = candidates[0];
  const previewCandidates = candidates.slice(0, 3);
  const hypothesisRows = hypotheses ?? [];
  const chainRows = propagationChain ?? [];
  const rootCauseEntities =
    summary.rootCauseEntities && summary.rootCauseEntities.length > 0
      ? summary.rootCauseEntities
      : (primaryCandidate?.entities ?? []);
  const displayedEntities = rootCauseEntities.slice(0, 3);
  const hiddenEntityCount = Math.max(
    0,
    rootCauseEntities.length - displayedEntities.length,
  );
  const hiddenCandidateCount = Math.max(
    0,
    candidates.length - previewCandidates.length,
  );

  return (
    <section
      className="diagnosis-workspace-report-card"
      data-testid="diagnosis-report-card"
    >
      <header className="diagnosis-workspace-report-card__header">
        <div>
          <div className="diagnosis-workspace-report-card__eyebrow">
            <ToneBadge tone="neutral">{"\u6839\u56e0\u8bca\u65ad"}</ToneBadge>
            {summary.priorityLabel ? (
              <ToneBadge tone="warning">{summary.priorityLabel}</ToneBadge>
            ) : null}
            <ToneBadge tone={summary.certaintyTone}>
              {summary.certaintyLabel}
            </ToneBadge>
            {planStatusLabel ? (
              <ToneBadge tone={planStatusTone ?? "info"}>
                {planStatusLabel}
              </ToneBadge>
            ) : null}
          </div>
          <h3 className="diagnosis-workspace-report-card__title">
            {summary.title}
          </h3>
          <p className="diagnosis-workspace-report-card__subtitle">
            {summary.subtitle}
          </p>
        </div>
        <div className="diagnosis-workspace-report-card__meta">
          {summary.sessionLabel ? <span>{summary.sessionLabel}</span> : null}
          <span>{summary.updatedDateTimeLabel ?? "--"}</span>
        </div>
      </header>

      <div className="diagnosis-workspace-report-card__body">
        <section className="diagnosis-workspace-report-card__hero">
          <div className="diagnosis-workspace-report-card__hero-main">
            <p className="diagnosis-workspace-report-card__section-label">
              {"\u5f53\u524d\u7ed3\u8bba"}
            </p>
            <h4 className="diagnosis-workspace-report-card__hero-title">
              {summary.rootCause ?? primaryCandidate?.title ?? "--"}
            </h4>
            <p className="diagnosis-workspace-report-card__hero-copy">
              {summary.impactSummary}
            </p>
            <div className="diagnosis-workspace-report-card__chip-row">
              <span className="diagnosis-workspace-report-card__chip">
                {summary.rootCauseLayerLabel ??
                  summary.rootCauseLayer ??
                  primaryCandidate?.layer ??
                  "--"}
              </span>
              {displayedEntities.map((entity) => (
                <span
                  className="diagnosis-workspace-report-card__chip"
                  key={entity}
                >
                  {entity}
                </span>
              ))}
              {hiddenEntityCount > 0 ? (
                <span className="diagnosis-workspace-report-card__chip">
                  {`+${hiddenEntityCount}`}
                </span>
              ) : null}
            </div>
          </div>

          <div className="diagnosis-workspace-report-card__stats">
            <div className="diagnosis-workspace-report-card__stat">
              <span>{"\u786e\u5b9a\u6027"}</span>
              <strong>{summary.certaintyLabel}</strong>
            </div>
            <div className="diagnosis-workspace-report-card__stat">
              <span>{"\u7f6e\u4fe1\u5ea6"}</span>
              <strong>
                {summary.confidenceRawLabel ?? summary.confidenceLabel}
              </strong>
            </div>
            <div className="diagnosis-workspace-report-card__stat">
              <span>{"\u4f18\u5148\u7ea7"}</span>
              <strong>{summary.priorityLabel ?? "--"}</strong>
            </div>
            <div className="diagnosis-workspace-report-card__stat">
              <span>{"\u66f4\u65b0\u65f6\u95f4"}</span>
              <strong>{summary.updatedTimeLabel ?? "--"}</strong>
            </div>
          </div>
        </section>

        <div className="diagnosis-workspace-report-card__snapshot-grid">
          <section className="diagnosis-workspace-report-card__panel">
            <p className="diagnosis-workspace-report-card__section-label">
              {"\u5019\u9009\u6839\u56e0"}
            </p>
            {previewCandidates.length > 0 ? (
              <div className="diagnosis-workspace-report-card__candidate-previews">
                {previewCandidates.map((candidate, index) => (
                  <article
                    className="diagnosis-workspace-report-card__candidate-preview"
                    key={candidate.id}
                  >
                    <div className="diagnosis-workspace-report-card__candidate-preview-header">
                      <strong>
                        {`#${candidate.rank ?? index + 1} ${candidate.title}`}
                      </strong>
                      <ToneBadge tone={candidate.statusTone}>
                        {candidate.statusLabel}
                      </ToneBadge>
                    </div>
                    <p className="diagnosis-workspace-report-card__section-copy">
                      {candidate.evidenceSummary ?? candidate.summary}
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <p className="diagnosis-workspace-report-card__section-copy">
                {"\u6682\u65e0\u5019\u9009\u6839\u56e0\u3002"}
              </p>
            )}
            {hiddenCandidateCount > 0 ? (
              <p className="diagnosis-workspace-report-card__footnote">
                {`\u53e6\u6709 ${hiddenCandidateCount} \u4e2a\u5019\u9009\u5df2\u6298\u53e0\u5230\u8be6\u60c5`}
              </p>
            ) : null}
          </section>

          <section className="diagnosis-workspace-report-card__panel">
            <p className="diagnosis-workspace-report-card__section-label">
              {"\u5f71\u54cd\u8303\u56f4"}
            </p>
            <div className="diagnosis-workspace-report-card__facts-inline">
              <div>
                <span>{"\u53d7\u5f71\u54cd\u670d\u52a1"}</span>
                <strong>
                  {summary.affectedServices.length > 0
                    ? summary.affectedServices.join("\uFF0C")
                    : "--"}
                </strong>
              </div>
              <div>
                <span>{"\u5b9e\u4f53"}</span>
                <strong>
                  {rootCauseEntities.length > 0
                    ? rootCauseEntities.join("\uFF0C")
                    : "--"}
                </strong>
              </div>
            </div>
            <p className="diagnosis-workspace-report-card__section-copy diagnosis-workspace-report-card__section-copy--muted">
              {planStatusLabel ??
                "\u4fee\u590d\u65b9\u6848\u5c06\u5728\u5ba1\u6279\u533a\u57df\u5c55\u793a\uff0c\u6b64\u5904\u4f18\u5148\u4fdd\u7559\u8bca\u65ad\u4e0a\u4e0b\u6587\u3002"}
            </p>
          </section>
        </div>

        <details className="diagnosis-workspace-report-card__details">
          <summary className="diagnosis-workspace-report-card__details-summary">
            {"\u5c55\u5f00\u8bc1\u636e\u3001\u5047\u8bbe\u4e0e\u4f20\u64ad\u94fe\u8def"}
          </summary>
          <div className="diagnosis-workspace-report-card__details-grid">
            <section className="diagnosis-workspace-report-card__section">
              <p className="diagnosis-workspace-report-card__section-label">
                {"\u5019\u9009\u8be6\u60c5"}
              </p>
              <div className="diagnosis-workspace-report-card__candidates">
                {candidates.length > 0 ? (
                  candidates.map((candidate, index) => (
                    <article
                      key={candidate.id}
                      className="diagnosis-workspace-report-card__candidate-item"
                    >
                      <div className="diagnosis-workspace-report-card__candidate-header">
                        <strong>
                          {`#${candidate.rank ?? index + 1} ${candidate.title}`}
                        </strong>
                        <span>{candidate.confidenceLabel}</span>
                      </div>
                      <p className="diagnosis-workspace-report-card__section-copy">
                        {candidate.evidenceSummary ?? candidate.summary}
                      </p>
                      {candidate.distinguishingVerification ? (
                        <p className="diagnosis-workspace-report-card__section-copy diagnosis-workspace-report-card__section-copy--muted">
                          {`\u533a\u5206\u9a8c\u8bc1\uff1a${candidate.distinguishingVerification}`}
                        </p>
                      ) : null}
                    </article>
                  ))
                ) : (
                  <p className="diagnosis-workspace-report-card__section-copy">
                    {"\u6682\u65e0\u5019\u9009\u6839\u56e0\u3002"}
                  </p>
                )}
              </div>
            </section>

            <section className="diagnosis-workspace-report-card__section">
              <p className="diagnosis-workspace-report-card__section-label">
                {"\u5047\u8bbe\u4e0e\u8bc1\u636e"}
              </p>
              <div className="diagnosis-workspace-report-card__hypotheses">
                {hypothesisRows.length > 0 ? (
                  hypothesisRows.map((entry, index) => (
                    <article
                      key={entry.id}
                      className="diagnosis-workspace-report-card__hypothesis-item"
                    >
                      <div className="diagnosis-workspace-report-card__hypothesis-header">
                        <strong>
                          {`${String.fromCharCode(65 + index)}. ${entry.description}`}
                        </strong>
                        <ToneBadge tone={entry.statusTone}>
                          {entry.statusLabel}
                        </ToneBadge>
                      </div>
                      <p className="diagnosis-workspace-report-card__section-copy">
                        {`\u652f\u6301\u8bc1\u636e ${entry.evidenceForCount} \u6761 | \u53cd\u8bc1 ${entry.evidenceAgainstCount} \u6761 | \u7f6e\u4fe1\u5ea6 ${entry.confidence.toFixed(2)}`}
                      </p>
                    </article>
                  ))
                ) : (
                  <p className="diagnosis-workspace-report-card__section-copy">
                    {"\u6682\u65e0\u5047\u8bbe\u8bc1\u636e\u6570\u636e\u3002"}
                  </p>
                )}
              </div>
            </section>

            <section className="diagnosis-workspace-report-card__section">
              <p className="diagnosis-workspace-report-card__section-label">
                {"\u4f20\u64ad\u94fe\u8def"}
              </p>
              <div className="diagnosis-workspace-report-card__propagation-body">
                {chainRows.length > 0 ? (
                  chainRows.map((step) => (
                    <article
                      key={step.id}
                      className="diagnosis-workspace-report-card__propagation-item"
                    >
                      <p>
                        {`${step.entityId} -> ${step.metric} -> ${step.valueBefore} -> ${step.valueAfter} -> ${step.description}`}
                      </p>
                    </article>
                  ))
                ) : (
                  <p className="diagnosis-workspace-report-card__section-copy">
                    {"\u6682\u65e0\u4f20\u64ad\u94fe\u8def\u6570\u636e\u3002"}
                  </p>
                )}
              </div>
            </section>
          </div>
        </details>
      </div>
    </section>
  );
}

function getSystemEventCategoryLabel(
  eventKind: Extract<DiagnosisTimelineItem, { kind: "system" }>['eventKind'],
) {
  switch (eventKind) {
    case "approval_result":
      return "审批反馈";
    case "canary_progress":
      return "灰度执行";
    case "metric_feedback":
      return "观察结论";
    case "alert_recovery":
      return "告警恢复";
    case "session_closed":
      return "诊断结束";
    default:
      return "系统事件";
  }
}
function SystemEventBlock({
  item,
}: {
  item: Extract<DiagnosisTimelineItem, { kind: "system" }>;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const hoverTime = formatTimestamp(item.timestamp);
  const categoryLabel = getSystemEventCategoryLabel(item.eventKind);
  const detailLines = useMemo(
    () => extractSystemEventDetailLines(item),
    [item.details, item.summary],
  );

  return (
    <article
      className="diagnosis-workspace-process-row diagnosis-workspace-process-row--system"
      title={hoverTime}
    >
      <div className="diagnosis-workspace-process-row__body">
        <button
          className="diagnosis-workspace-system-event__toggle"
          onClick={() => setIsExpanded((current) => !current)}
          type="button"
        >
          <span className="diagnosis-workspace-thinking__icon" aria-hidden="true">
            <span
              className={cn(
                "diagnosis-workspace-thinking__chevron",
                isExpanded && "diagnosis-workspace-thinking__chevron--expanded",
              )}
            />
          </span>
          <span className="diagnosis-workspace-system-event__label-wrap">
            <ToneBadge tone="neutral">{categoryLabel}</ToneBadge>
            <span className="diagnosis-workspace-system-event__summary">
              {item.summary}
            </span>
          </span>
          <span className="diagnosis-workspace-system-event__time">
            {hoverTime}
          </span>
        </button>
        {item.progress ? (
          <div className="diagnosis-workspace-system-event__progress">
            <div className="diagnosis-workspace-system-event__progress-row">
              <span>{item.progress.label}</span>
              <strong>{item.progress.value}%</strong>
            </div>
            <div className="progress-track remediation-progress-track remediation-progress-track--canary">
              <div
                className="progress-track__fill remediation-progress-track__fill remediation-progress-track__fill--canary"
                style={{ width: `${item.progress.value}%` }}
              />
            </div>
            {item.progress.helper ? <p>{item.progress.helper}</p> : null}
          </div>
        ) : null}

        {isExpanded ? (
          <div className="diagnosis-workspace-system-event__panel">
            <div className="diagnosis-workspace-system-event__content">
              {detailLines.map((detail, index) => (
                <p key={`${item.id}-${index}`}>{detail}</p>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function ApprovalResultThoughtBlock({
  item,
}: {
  item: Extract<DiagnosisTimelineItem, { kind: "system" }>;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const hoverTime = formatTimestamp(item.timestamp);
  const detailLines = useMemo(
    () => extractApprovalResultDetailLines(item),
    [item.details, item.summary],
  );
  const thoughtDurationLabel = formatThoughtDurationLabel(
    estimateThoughtDurationSecFromContent(detailLines.join(" ")),
  );

  return (
    <article
      className="diagnosis-workspace-process-row diagnosis-workspace-process-row--system"
      title={hoverTime}
    >
      <div className="diagnosis-workspace-process-row__body">
        <button
          aria-expanded={isExpanded}
          className="diagnosis-workspace-thinking__toggle diagnosis-workspace-thinking__toggle--interactive"
          data-testid="diagnosis-approval-result-thought"
          onClick={() => setIsExpanded((current) => !current)}
          type="button"
        >
          <span
            className="diagnosis-workspace-thinking__icon"
            aria-hidden="true"
          >
            <span
              className={cn(
                "diagnosis-workspace-thinking__chevron",
                isExpanded &&
                  "diagnosis-workspace-thinking__chevron--expanded",
              )}
            />
          </span>
          <span className="diagnosis-workspace-thinking__label-wrap">
            <span className="diagnosis-workspace-thinking__label">
              {thoughtDurationLabel}
            </span>
          </span>
        </button>

        {isExpanded ? (
          <div className="diagnosis-workspace-thinking__panel">
            <div className="diagnosis-workspace-thinking__content">
              {detailLines.map((detail, index) => (
                <p key={`${item.id}-${index}`}>{detail}</p>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function DemoApprovalCard({
  open,
  plan,
  summary,
  rejectReason,
  rejectExpanded,
  isSubmitting,
  resolution,
  onRejectReasonChange,
  onExpandReject,
  onCancelReject,
  onApprove,
  onReject,
}: {
  open: boolean;
  plan?: DiagnosisPlanView;
  summary?: DiagnosisSummaryView;
  rejectReason: string;
  rejectExpanded: boolean;
  isSubmitting: boolean;
  resolution: DemoApprovalDecision | null;
  onRejectReasonChange: (value: string) => void;
  onExpandReject: () => void;
  onCancelReject: () => void;
  onApprove: () => void;
  onReject: () => void;
}) {
  const surfaceView = buildApprovalSurfaceView({
    plan,
    summary,
    planVersion: null,
    statusLabel:
      resolution === "approved"
        ? "\u5df2\u6279\u51c6\u6267\u884c"
        : resolution === "rejected"
          ? "\u5df2\u62d2\u7edd\u6267\u884c"
          : "\u5f85\u5ba1\u6279",
    statusTone:
      resolution === "approved"
        ? "success"
        : resolution === "rejected"
          ? "danger"
          : "info",
  });
  const resolutionState: ApprovalSurfaceResolution | null =
    resolution === null
      ? null
      : {
          label: resolution === "approved" ? "\u5df2\u6279\u51c6\u6267\u884c" : "\u5df2\u62d2\u7edd\u6267\u884c",
          tone: resolution === "approved" ? "success" : "danger",
          description:
            resolution === "approved"
              ? "\u6f14\u793a\u5ba1\u6279\u5df2\u8bb0\u5f55\u901a\u8fc7\uff0c\u540e\u7eed\u4f1a\u7ee7\u7eed\u5c55\u793a\u6267\u884c\u4e0e\u9a8c\u8bc1\u8fdb\u5ea6\u3002"
              : rejectReason.trim()
                ? `\u6f14\u793a\u5ba1\u6279\u5df2\u8bb0\u5f55\u62d2\u7edd\uff1a${rejectReason.trim()}`
                : "\u6f14\u793a\u5ba1\u6279\u5df2\u8bb0\u5f55\u5f53\u524d\u65b9\u6848\u88ab\u62d2\u7edd\u6267\u884c\u3002",
        };

  return (
    <ApprovalSurface
      approveButtonTestId="diagnosis-demo-approve-button"
      canApprove
      dataTestId="diagnosis-demo-approval-card"
      isSubmitting={isSubmitting}
      onApprove={onApprove}
      onCancelReject={onCancelReject}
      onExpandReject={onExpandReject}
      onReject={onReject}
      onRejectReasonChange={onRejectReasonChange}
      open={open}
      rejectButtonTestId="diagnosis-demo-reject-button"
      rejectExpanded={rejectExpanded}
      rejectReason={rejectReason}
      rejectReasonId="diagnosis-demo-approval-reason"
      rejectReasonTestId="diagnosis-demo-approval-reason"
      resolution={resolutionState}
      surfaceView={surfaceView}
      variant="floating"
    />
  );
}

function ApprovalSurface({
  open,
  surfaceView,
  resolution,
  rejectReason,
  rejectExpanded,
  canApprove,
  approvalBlockReason,
  isSubmitting,
  onRejectReasonChange,
  onExpandReject,
  onCancelReject,
  onApprove,
  onReject,
  dataTestId,
  rejectButtonTestId,
  approveButtonTestId,
  rejectReasonTestId,
  rejectReasonId,
  variant,
}: {
  open: boolean;
  surfaceView: ApprovalSurfaceView | null;
  resolution: ApprovalSurfaceResolution | null;
  rejectReason: string;
  rejectExpanded: boolean;
  canApprove: boolean;
  approvalBlockReason?: string;
  isSubmitting: boolean;
  onRejectReasonChange: (value: string) => void;
  onExpandReject: () => void;
  onCancelReject: () => void;
  onApprove: () => void;
  onReject: () => void;
  dataTestId: string;
  rejectButtonTestId: string;
  approveButtonTestId: string;
  rejectReasonTestId: string;
  rejectReasonId: string;
  variant: "floating" | "inline";
}) {
  const [showTechnicalDetails, setShowTechnicalDetails] = useState(false);

  useEffect(() => {
    setShowTechnicalDetails(false);
  }, [surfaceView?.title, resolution?.label]);

  if (!open || !surfaceView) {
    return null;
  }

  const surfaceTone = resolution?.tone ?? surfaceView.statusTone;
  const surfaceLabel = resolution?.label ?? surfaceView.statusLabel;
  const rejectSubmitDisabled =
    isSubmitting || !canApprove || !rejectReason.trim();

  return (
    <section
      aria-live="polite"
      className={cn(
        "diagnosis-workspace-approval-surface",
        `diagnosis-workspace-approval-surface--${variant}`,
        resolution?.tone === "success" &&
          "diagnosis-workspace-approval-surface--approved",
        resolution?.tone === "danger" &&
          "diagnosis-workspace-approval-surface--rejected",
      )}
      data-testid={dataTestId}
    >
      <header className="diagnosis-workspace-approval-surface__header">
        <div>
          <div className="diagnosis-workspace-approval-surface__eyebrow">
            <ToneBadge tone={surfaceTone}>{surfaceLabel}</ToneBadge>
          </div>
          <h3 className="diagnosis-workspace-approval-surface__title">
            {surfaceView.title}
          </h3>
          <p className="diagnosis-workspace-approval-surface__description">
            {surfaceView.description}
          </p>
        </div>
      </header>

      <div className="diagnosis-workspace-approval-surface__body">
        {surfaceView.metaItems.length > 0 ? (
          <div className="diagnosis-workspace-approval-surface__meta-row">
            {surfaceView.metaItems.map((item) => (
              <span
                className="diagnosis-workspace-approval-surface__meta-item"
                key={item}
              >
                {item}
              </span>
            ))}
          </div>
        ) : null}

        <section className="diagnosis-workspace-approval-surface__impact">
          <span className="diagnosis-workspace-approval-surface__section-label">
            {surfaceView.impactLabel}
          </span>
          <p>{surfaceView.impactSummary}</p>
        </section>

        <section className="diagnosis-workspace-approval-surface__steps">
          <span className="diagnosis-workspace-approval-surface__section-label">
            \u6267\u884c\u6b65\u9aa4
          </span>
          <ol className="diagnosis-workspace-approval-surface__actions">
            {surfaceView.steps.map((step, index) => (
              <li key={step.id}>
                <span>{index + 1}</span>
                <div>
                  <strong>{step.title}</strong>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {surfaceView.hasTechnicalDetails ? (
          <div className="diagnosis-workspace-approval-surface__details">
            <button
              className="diagnosis-workspace-approval-surface__details-toggle"
              onClick={() =>
                setShowTechnicalDetails((current) => !current)
              }
              type="button"
            >
              {showTechnicalDetails ? "\u6536\u8d77\u6267\u884c\u7ec6\u8282" : "\u67e5\u770b\u6267\u884c\u7ec6\u8282"}
            </button>
            {showTechnicalDetails ? (
              <div className="diagnosis-workspace-approval-surface__details-body">
                {surfaceView.steps.map((step, index) => {
                  const detailLines = getApprovalStepDetailLines(step);
                  if (detailLines.length === 0) {
                    return null;
                  }

                  return (
                    <div
                      className="diagnosis-workspace-approval-surface__details-item"
                      key={`${step.id}-details`}
                    >
                      <strong>{`\u6b65\u9aa4 ${index + 1} ${step.title}`}</strong>
                      {detailLines.map((line, lineIndex) => (
                        <p key={`${step.id}-${lineIndex}`}>{line}</p>
                      ))}
                    </div>
                  );
                })}
              </div>
            ) : null}
          </div>
        ) : null}

        {isSubmitting ? (
          <div className="diagnosis-workspace-approval-surface__updating">
            \u6b63\u5728\u63d0\u4ea4\u786e\u8ba4...
          </div>
        ) : null}

        {resolution ? (
          <div className="diagnosis-workspace-approval-surface__resolved">
            <ToneBadge tone={resolution.tone}>{resolution.label}</ToneBadge>
            <p>{resolution.description}</p>
          </div>
        ) : null}

        {!resolution && rejectExpanded ? (
          <div className="diagnosis-workspace-approval-surface__editor">
            <label htmlFor={rejectReasonId}>\u62d2\u7edd\u539f\u56e0\uff08\u62d2\u7edd\u65f6\u5fc5\u586b\uff09</label>
            <textarea
              data-testid={rejectReasonTestId}
              id={rejectReasonId}
              onChange={(event) => onRejectReasonChange(event.target.value)}
              placeholder="\u8bf7\u8bf4\u660e\u4e3a\u4ec0\u4e48\u4e0d\u540c\u610f\u6267\u884c\u8be5\u4fee\u590d\u65b9\u6848"
              rows={3}
              value={rejectReason}
            />
            {approvalBlockReason ? (
              <p className="diagnosis-workspace-approval-surface__note diagnosis-workspace-approval-surface__note--warning">
                {approvalBlockReason}
              </p>
            ) : null}
          </div>
        ) : null}

        {!resolution ? (
          <div className="diagnosis-workspace-approval-surface__footer">
            {rejectExpanded ? (
              <button
                className="diagnosis-workspace-action-btn"
                onClick={onCancelReject}
                type="button"
              >
                \u53d6\u6d88
              </button>
            ) : null}
            <button
              className="diagnosis-workspace-action-btn"
              data-testid={rejectButtonTestId}
              disabled={
                rejectExpanded ? rejectSubmitDisabled : isSubmitting || !canApprove
              }
              onClick={rejectExpanded ? onReject : onExpandReject}
              type="button"
            >
              {isSubmitting ? "\u6b63\u5728\u63d0\u4ea4..." : rejectExpanded ? "\u786e\u8ba4\u62d2\u7edd" : "\u62d2\u7edd"}
            </button>
            <button
              className="diagnosis-workspace-action-btn diagnosis-workspace-action-btn--primary"
              data-testid={approveButtonTestId}
              disabled={isSubmitting || !canApprove}
              onClick={onApprove}
              type="button"
            >
              {isSubmitting ? "\u6b63\u5728\u63d0\u4ea4..." : "\u540c\u610f\u6267\u884c"}
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function ApprovalOverlay({
  open,
  plan,
  summary,
  statusLabel,
  statusTone,
  planVersion,
  rejectReason,
  rejectExpanded,
  canApprove,
  approvalBlockReason,
  isSubmitting,
  onRejectReasonChange,
  onExpandReject,
  onCancelReject,
  onApprove,
  onReject,
}: {
  open: boolean;
  plan?: DiagnosisPlanView;
  summary?: DiagnosisSummaryView;
  statusLabel: string;
  statusTone: BadgeTone;
  planVersion: number | null;
  rejectReason: string;
  rejectExpanded: boolean;
  canApprove: boolean;
  approvalBlockReason?: string;
  isSubmitting: boolean;
  onRejectReasonChange: (value: string) => void;
  onExpandReject: () => void;
  onCancelReject: () => void;
  onApprove: () => void;
  onReject: () => void;
}) {
  const surfaceView = buildApprovalSurfaceView({
    plan,
    summary,
    planVersion,
    statusLabel,
    statusTone,
  });

  return (
    <ApprovalSurface
      approvalBlockReason={approvalBlockReason}
      approveButtonTestId="diagnosis-approve-button"
      canApprove={canApprove}
      dataTestId="diagnosis-approval-overlay"
      isSubmitting={isSubmitting}
      onApprove={onApprove}
      onCancelReject={onCancelReject}
      onExpandReject={onExpandReject}
      onReject={onReject}
      onRejectReasonChange={onRejectReasonChange}
      open={open}
      rejectButtonTestId="diagnosis-reject-button"
      rejectExpanded={rejectExpanded}
      rejectReason={rejectReason}
      rejectReasonId="diagnosis-approval-reason"
      rejectReasonTestId="diagnosis-approval-reason"
      resolution={null}
      surfaceView={surfaceView}
      variant="floating"
    />
  );
}

function findTimelineToolStatus(
  timelineItems: DiagnosisTimelineItem[],
  toolId: string,
) {
  for (const item of timelineItems) {
    if (item.kind === "tool" && item.id === toolId) {
      return item.status;
    }
    if (item.kind === "run") {
      const tool = item.tools.find((entry) => entry.id === toolId);
      if (tool) {
        return tool.status;
      }
    }
  }
  return undefined;
}

function getLoadingToolIds(item: DiagnosisTimelineItem) {
  if (item.kind === "tool" && item.status === "loading") {
    return [item.id];
  }
  if (item.kind === "run") {
    return item.tools
      .filter((tool) => tool.status === "loading")
      .map((tool) => tool.id);
  }
  return [];
}

function markTimelineToolTimeout(
  timelineItems: DiagnosisTimelineItem[],
  toolId: string,
): DiagnosisTimelineItem[] {
  const timeoutSummary = "Timed out after 15s waiting for tool_result.";
  return timelineItems.map((item) => {
    if (item.kind === "tool") {
      if (item.id !== toolId || item.status !== "loading") {
        return item;
      }
      return {
        ...item,
        status: "timeout" as const,
        summaryLines: item.summaryLines.includes(timeoutSummary)
          ? item.summaryLines
          : [...item.summaryLines, timeoutSummary],
      };
    }

    if (item.kind !== "run") {
      return item;
    }

    let changed = false;
    const tools = item.tools.map((tool) => {
      if (tool.id !== toolId || tool.status !== "loading") {
        return tool;
      }
      changed = true;
      return {
        ...tool,
        status: "timeout" as const,
        summaryLines: tool.summaryLines.includes(timeoutSummary)
          ? tool.summaryLines
          : [...tool.summaryLines, timeoutSummary],
      };
    });

    if (!changed) {
      return item;
    }

    const steps = item.steps.map((step) =>
      step.toolIds.includes(toolId) && step.status === "running"
        ? { ...step, status: "timeout" as const, statusTone: "danger" as const }
        : step,
    );

    return {
      ...item,
      status: "timeout" as const,
      currentStageLabel: "\u6267\u884c\u8d85\u65f6",
      tools,
      steps,
    };
  });
}

function DiagnosisPage() {
  const params = useParams<{ sessionId?: string }>();
  const routeSessionId = (params.sessionId ?? "").trim();
  const shouldBootstrapLiveSession = routeSessionId.length > 0;
  const [draft, setDraft] = useState("");
  const [demoTimeline, setDemoTimeline] = useState<
    DiagnosisTimelineItem[]
  >([]);
  const [, setDemoCandidates] = useState<DiagnosisCandidateView[]>([]);
  const [demoSummary, setDemoSummary] = useState<
    DiagnosisSummaryView | undefined
  >();
  const [demoPlan, setDemoPlan] = useState<
    DiagnosisPlanView | undefined
  >();
  const [, setDemoHypotheses] = useState<DiagnosisHypothesisView[]>([]);
  const [, setDemoPropagationChain] = useState<DiagnosisPropagationStepView[]>([]);
  const [demoState, setDemoState] = useState<"idle" | "running" | "complete">(
    "idle",
  );
  const [lastDemoPrompt, setLastDemoPrompt] = useState("");
  const [demoApprovalCardOpen, setDemoApprovalCardOpen] = useState(false);
  const [demoApprovalReason, setDemoApprovalReason] = useState("");
  const [demoRejectEditorOpen, setDemoRejectEditorOpen] = useState(false);
  const [demoApprovalDecision, setDemoApprovalDecision] =
    useState<DemoApprovalDecision | null>(null);
  const [isSubmittingDemoApproval, setIsSubmittingDemoApproval] =
    useState(false);
  const [approvalReason, setApprovalReason] = useState("");
  const [approvalRejectEditorOpen, setApprovalRejectEditorOpen] =
    useState(false);
  const [activeStreamingMessageId, setActiveStreamingMessageId] = useState<
    string | null
  >(null);
  const [liveTimeline, setLiveTimeline] = useState<
    DiagnosisTimelineItem[]
  >([]);

  const feedRef = useRef<HTMLDivElement | null>(null);
  const pendingAutoScrollFrameRef = useRef<number | null>(null);
  const autoScrollEnabledRef = useRef(true);
  const demoTimerRef = useRef<number[]>([]);
  const demoRunTokenRef = useRef(0);
  const demoApprovalRevealTimerRef = useRef<number | null>(null);
  const demoApprovalSubmitTimerRef = useRef<number | null>(null);

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

  const liveTimelineRef = useRef<DiagnosisTimelineItem[]>([]);
  const liveQueuedItemsRef = useRef<DiagnosisTimelineItem[]>([]);
  const liveQueuedIdsRef = useRef<Set<string>>(new Set());
  const liveDisplayedIdsRef = useRef<Set<string>>(new Set());
  const liveQueueProcessingRef = useRef(false);
  const liveQueueTokenRef = useRef(0);
  const latestLiveSourceByIdRef = useRef<
    Map<string, DiagnosisTimelineItem>
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
    () => buildDiagnosisLiveView(session, messages, events, localAuditRecords),
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
    setApprovalRejectEditorOpen(false);
  }, [activeSessionId, latestPlanVersion]);

  const handleApprovePlan = useCallback(async () => {
    try {
      await approvePlan({ approved: true });
      setApprovalReason("");
      setApprovalRejectEditorOpen(false);
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
      setApprovalRejectEditorOpen(false);
    } catch {
      // Store error state handles UI recovery.
    }
  }, [approvalReason, approvePlan]);

  const clearDemoTimers = useCallback(() => {
    demoTimerRef.current.forEach((timerId) => window.clearTimeout(timerId));
    demoTimerRef.current = [];
  }, []);

  const clearDemoApprovalTimers = useCallback(() => {
    if (demoApprovalRevealTimerRef.current) {
      window.clearTimeout(demoApprovalRevealTimerRef.current);
      demoApprovalRevealTimerRef.current = null;
    }
    if (demoApprovalSubmitTimerRef.current) {
      window.clearTimeout(demoApprovalSubmitTimerRef.current);
      demoApprovalSubmitTimerRef.current = null;
    }
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
      clearDemoApprovalTimers();
      clearPendingMessageStreams();
      clearPendingThinkingStreams();
      clearDemoToolLoadingStates();
      clearLiveToolWaiters();
    },
    [
      clearDemoApprovalTimers,
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
    (timelineItems: DiagnosisTimelineItem[]) => {
      for (const [toolId, resolver] of [
        ...liveToolWaiterResolversRef.current.entries(),
      ]) {
        const status = findTimelineToolStatus(timelineItems, toolId);
        if (status && status !== "loading") {
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
      const existingToolStatus = findTimelineToolStatus(
        liveTimelineRef.current,
        toolId,
      );
      if (existingToolStatus && existingToolStatus !== "loading") {
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
          setLiveTimeline((current) => markTimelineToolTimeout(current, toolId));
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

        const loadingToolIds = getLoadingToolIds(nextItem);
        for (const loadingToolId of loadingToolIds) {
          await waitForLiveToolTerminal(loadingToolId);
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

  const playDemoApprovedRemediationFlow = useCallback(
    (runToken: number) => {
      const startedAt = Date.now();
      const timestampAt = (offsetMs: number) =>
        new Date(startedAt + offsetMs).toISOString();
      const appendItem = (item: DiagnosisTimelineItem) => {
        setDemoTimeline((current) => {
          if (current.some((existing) => existing.id === item.id)) {
            return current;
          }
          return [...current, item];
        });
      };
      const wait = async (delayMs: number) => {
        await waitForDemoDelay(delayMs, runToken);
        return runToken === demoRunTokenRef.current;
      };
      const appendAssistantMessage = async (
        id: string,
        content: string,
        offsetMs: number,
      ) => {
        if (runToken !== demoRunTokenRef.current) {
          return false;
        }
        const item: DiagnosisTimelineItem = {
          id,
          kind: "message",
          role: "assistant",
          content,
          timestamp: timestampAt(offsetMs),
        };
        appendItem(item);
        await waitForMessageStream(id, content);
        return runToken === demoRunTokenRef.current;
      };
      const appendSystemEvent = (
        id: string,
        eventKind: Extract<DiagnosisTimelineItem, { kind: "system" }>["eventKind"],
        summary: string,
        details: string[],
        statusTone: BadgeTone,
        offsetMs: number,
        progress?: Extract<DiagnosisTimelineItem, { kind: "system" }>["progress"],
      ) => {
        appendItem({
          id,
          kind: "system",
          eventKind,
          summary: normalizeDiagnosisDisplayText(summary),
          details: details.map((detail) => normalizeDiagnosisDisplayText(detail)),
          timestamp: timestampAt(offsetMs),
          statusTone,
          progress: progress
            ? {
                ...progress,
                label: normalizeDiagnosisDisplayText(progress.label),
                helper: progress.helper
                  ? normalizeDiagnosisDisplayText(progress.helper)
                  : undefined,
              }
            : undefined,
          source: "optimistic",
          dedupeKey: id,
        });
      };
      const appendTool = (
        id: string,
        toolName: string,
        params: Record<string, unknown>,
        summaryLines: string[],
        offsetMs: number,
      ) => {
        appendItem({
          id,
          kind: "tool",
          toolName,
          params,
          timestamp: timestampAt(offsetMs),
          status: "loading",
          summaryLines: summaryLines.map((line) => normalizeDiagnosisDisplayText(line)),
        });
      };
      const completeTool = (
        id: string,
        summaryLines: string[],
        rawResult?: Record<string, unknown>,
      ) => {
        setDemoTimeline((current) =>
          current.map((item) => {
            if (item.kind !== "tool" || item.id !== id) {
              return item;
            }
            return {
              ...item,
              status: "success",
              summaryLines: summaryLines.map((line) => normalizeDiagnosisDisplayText(line)),
              rawResult,
            };
          }),
        );
      };

      void (async () => {
        const canarySkillToolId = `demo-post-approval-canary-skill-${startedAt}`;
        const fullSkillToolId = `demo-post-approval-full-skill-${startedAt}`;

                appendSystemEvent(
          `demo-approval-confirmed-${startedAt}`,
          "approval_result",
          "[绯荤粺] 宸茬粡瀹屾垚鎵ц纭",
          [
            "审批反馈：已经完成执行确认",
            "鎵ц杈圭晫锛氬厛鐏板害锛屾寚鏍囩‘璁ゅ悗鍐嶅仛鍏ㄩ噺淇",
          ],
          "success",
          0,
        );
        if (!(await wait(420))) return;

        if (!(await appendAssistantMessage(
          `demo-canary-start-message-${startedAt}`,
          "已收到确认。现在开始灰度，先调用 vLLM 诊断 skill 观察灰度窗口的延迟、错误率和 GPU 利用率。",
          420,
        ))) return;
        if (!(await wait(360))) return;

        appendSystemEvent(
          `demo-canary-started-${startedAt}`,
          "canary_progress",
          "[绯荤粺] 寮€濮嬬伆搴︼細璋冪敤 skill 瑙傚療鐏板害绐楀彛",
          [
            "璋冪敤 skill锛歜uiltin-vllm-diagnosis",
            "鐏板害鑼冨洿锛?0% 娴侀噺",
            "瑙傚療瀵硅薄锛歷llm_p95_ms / inference_error_rate / GPU util",
          ],
          "warning",
          900,
          { label: "鐏板害杩涘害", value: 10, helper: "canary 10%" },
        );
        appendTool(
          canarySkillToolId,
          "run_skill",
          {
            skill_id: "builtin-vllm-diagnosis",
            phase: "canary",
            namespace: "service",
            node: "worker-03",
          },
          ["姝ｅ湪璋冪敤 builtin-vllm-diagnosis 閲囬泦鐏板害绐楀彛鎸囨爣..."],
          980,
        );
        if (!(await wait(1250))) return;

        appendSystemEvent(
          `demo-canary-progress-45-${startedAt}`,
          "canary_progress",
          "[系统] 灰度执行中：首批实例已完成切换",
          ["鐏板害杩涘害锛?5%", "褰撳墠 p95锛?.72s", "閿欒鐜囷細0.4%"],
          "warning",
          2150,
          { label: "鐏板害杩涘害", value: 45, helper: "棣栨壒瀹炰緥" },
        );
        if (!(await wait(900))) return;

        appendSystemEvent(
          `demo-canary-progress-80-${startedAt}`,
          "canary_progress",
          "[绯荤粺] 鐏板害鎵ц涓細鎸囨爣鎸佺画鏀舵暃",
          ["鐏板害杩涘害锛?0%", "GPU util锛?9% -> 74%", "闃熷垪绛夊緟锛氫笅闄?68%"],
          "warning",
          3050,
          { label: "鐏板害杩涘害", value: 80, helper: "鎸囨爣鏀舵暃" },
        );
        if (!(await wait(860))) return;

        completeTool(
          canarySkillToolId,
          [
            "skill builtin-vllm-diagnosis 鎵ц瀹屾垚",
            "vllm_p95_ms: 3.4s -> 1.68s",
            "inference_error_rate: 2.7% -> 0.4%",
          ],
          {
            skill_id: "builtin-vllm-diagnosis",
            vllm_p95_ms: 1680,
            inference_error_rate: 0.004,
          },
        );
        appendSystemEvent(
          `demo-canary-complete-${startedAt}`,
          "canary_progress",
          "[绯荤粺] 宸插畬鎴愮伆搴︼細绛夊緟鎸囨爣鍙嶉纭鐏板害鏁堟灉",
          [
            "鐏板害杩涘害锛?00%",
            "涓嬩竴姝ワ細绛夊緟鎸囨爣鍙嶉纭鏄惁缁х画鍏ㄩ噺淇",
          ],
          "success",
          3920,
          { label: "鐏板害杩涘害", value: 100, helper: "绛夊緟鎸囨爣鍙嶉" },
        );
        if (!(await wait(760))) return;

        appendSystemEvent(
          `demo-canary-feedback-${startedAt}`,
          "metric_feedback",
          "[绯荤粺] 鎸囨爣鍙嶉宸茬‘璁わ細鍙嶉宸茬‘璁ょ伆搴︽病鏈夐棶棰橈紝杩涜鍏ㄩ噺淇",
          ["指标反馈：灰度没有问题", "vLLM p95 低于 1.8s", "错误率低于 1%"],
          "success",
          4680,
        );
        if (!(await wait(640))) return;

        appendSystemEvent(
          `demo-full-rollout-start-${startedAt}`,
          "execution_progress",
          "[系统] 开始全量修复：扩大到剩余实例",
          ["鎵ц鍔ㄤ綔锛歵raffic_restore", "绛栫暐锛歡radual", "鑼冨洿锛氬墿浣?90% 娴侀噺"],
          "warning",
          5320,
          { label: "鍏ㄩ噺杩涘害", value: 35, helper: "閫愭鏀鹃噺" },
        );
        if (!(await wait(720))) return;

        appendTool(
          fullSkillToolId,
          "run_skill",
          {
            skill_id: "builtin-platform-health",
            phase: "full_rollout_observation",
            namespace: "service",
            node: "worker-03",
          },
          ["姝ｅ湪璋冪敤 builtin-platform-health 瑙傚療鍏ㄩ噺淇鏁堟灉..."],
          6040,
        );
        appendSystemEvent(
          `demo-full-rollout-progress-${startedAt}`,
          "execution_progress",
          "[绯荤粺] 鍏ㄩ噺淇涓細鏀鹃噺杩涘害绋冲畾鎺ㄨ繘",
          ["全量进度：72%", "未观察到错误率反弹"],
          "warning",
          6080,
          { label: "鍏ㄩ噺杩涘害", value: 72, helper: "鍏ㄩ噺鏀鹃噺" },
        );
        if (!(await wait(1050))) return;

        appendSystemEvent(
          `demo-full-feedback-wait-${startedAt}`,
          "metric_feedback",
          "[绯荤粺] 绛夊緟鎸囨爣鍙嶉鍏ㄩ噺鏁堟灉",
          ["观察窗口：全量放量后 3 分钟", "检查项：延迟、错误率、告警状态"],
          "warning",
          7130,
        );
        if (!(await wait(980))) return;

        completeTool(
          fullSkillToolId,
          [
            "skill builtin-platform-health 鎵ц瀹屾垚",
            "vllm_p95_ms: 1.42s",
            "inference_error_rate: 0.1%",
            "alert_status: resolved",
          ],
          {
            skill_id: "builtin-platform-health",
            vllm_p95_ms: 1420,
            inference_error_rate: 0.001,
            alert_status: "resolved",
          },
        );
        appendSystemEvent(
          `demo-alert-recovered-${startedAt}`,
          "alert_recovery",
          "[绯荤粺] 鎶ヨ宸叉仮澶嶏細鐩稿叧鎶ヨ宸茬粡鎭㈠",
          ["鎶ヨ锛歷LLM 鎺ㄧ悊寤惰繜鍗囬珮", "鐘舵€侊細resolved", "鎭㈠鏉ユ簮锛欰lertmanager snapshot"],
          "success",
          8110,
          { label: "鎵ц杩涘害", value: 100, helper: "鎶ヨ鎭㈠" },
        );
        if (!(await wait(520))) return;

        appendSystemEvent(
          `demo-session-closed-${startedAt}`,
          "session_closed",
          "[系统] 诊断已关闭：结束并关闭诊断",
          ["闭环结果：修复完成", "后续动作：保留审计记录与执行明细"],
          "success",
          8630,
          { label: "鎵ц杩涘害", value: 100, helper: "浼氳瘽鍏抽棴" },
        );
      })();
    },
    [waitForDemoDelay, waitForMessageStream],
  );
  const startDemo = useCallback(
    (prompt: string) => {
      const normalizedPrompt = prompt.trim();
      if (!normalizedPrompt) {
        return;
      }

      demoRunTokenRef.current += 1;
      const runToken = demoRunTokenRef.current;

      clearDemoTimers();
      clearDemoApprovalTimers();
      clearPendingMessageStreams();
      clearPendingThinkingStreams();
      clearDemoToolLoadingStates();

      const scenario = buildDiagnosisDemoScenario(normalizedPrompt);
      setDemoTimeline(scenario.initialTimeline);
      setDemoCandidates([]);
      setDemoHypotheses([]);
      setDemoPropagationChain([]);
      setDemoSummary(undefined);
      setDemoPlan(undefined);
      setDemoState("running");
      setDemoApprovalCardOpen(false);
      setDemoApprovalReason("");
      setDemoRejectEditorOpen(false);
      setDemoApprovalDecision(null);
      setIsSubmittingDemoApproval(false);
      setLastDemoPrompt(normalizedPrompt);
      setDraft("");

      void (async () => {
        let previousDelay = 0;
        const activeDemoThinkingIds = new Set<string>();
        const activeDemoToolIds = new Set<string>();
        const knownDemoThinkingIds = new Set<string>();
        const knownDemoToolIds = new Set<string>();
        const deferredDemoEvents: DiagnosisDemoEvent[] = [];

        const canProcessDemoEvent = (event: DiagnosisDemoEvent) => {
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
          event: DiagnosisDemoEvent,
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
      clearDemoApprovalTimers,
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

  const activeRawTimeline = hasLiveSession ? liveTimeline : demoTimeline;
  const activeTimeline = useMemo(
    () =>
      groupExecutionRunTimeline(
        activeRawTimeline,
        hasLiveSession
          ? `${activeSessionId ?? session?.session_id ?? "live"}-execution-run`
          : "demo-execution-run",
      ),
    [activeRawTimeline, activeSessionId, hasLiveSession, session?.session_id],
  );
  const activeSummary = hasLiveSession ? liveView.summary : demoSummary;
  const activePlan = hasLiveSession ? liveView.plan : demoPlan;
  const activeReportReady = activeTimeline.some((item) => item.kind === "report");
  const activeApprovalStatusLabel =
    hasLiveSession && session ? formatWorkflowStatus(session.status) : "\u5f85\u5ba1\u6279";
  const approvalSurfaceOpen =
    Boolean(activePlan) &&
    activeReportReady &&
    (hasLiveSession ? approvalOverlayOpen : demoApprovalCardOpen);

  const isFeedNearBottom = useCallback((element: HTMLDivElement) => {
    const distanceFromBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    return distanceFromBottom <= AUTO_SCROLL_BOTTOM_THRESHOLD_PX;
  }, []);

  const scrollFeedToBottom = useCallback(() => {
    const feedElement = feedRef.current;
    if (!feedElement) {
      return;
    }

    feedElement.scrollTop = feedElement.scrollHeight;
  }, []);

  const scheduleFeedScrollToBottom = useCallback(() => {
    if (!autoScrollEnabledRef.current) {
      return;
    }

    if (pendingAutoScrollFrameRef.current !== null) {
      window.cancelAnimationFrame(pendingAutoScrollFrameRef.current);
    }

    pendingAutoScrollFrameRef.current = window.requestAnimationFrame(() => {
      pendingAutoScrollFrameRef.current = null;
      if (!autoScrollEnabledRef.current) {
        return;
      }

      scrollFeedToBottom();
    });
  }, [scrollFeedToBottom]);

  const handleFeedScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      autoScrollEnabledRef.current = isFeedNearBottom(event.currentTarget);
    },
    [isFeedNearBottom],
  );

  useEffect(() => {
    if (
      hasLiveSession ||
      demoState !== "complete" ||
      !demoSummary ||
      !demoPlan ||
      !activeReportReady
    ) {
      clearDemoApprovalTimers();
      setDemoApprovalCardOpen(false);
      setDemoApprovalReason("");
      setDemoRejectEditorOpen(false);
      setDemoApprovalDecision(null);
      setIsSubmittingDemoApproval(false);
      return;
    }

    clearDemoApprovalTimers();
    demoApprovalRevealTimerRef.current = window.setTimeout(() => {
      setDemoApprovalCardOpen(true);
      demoApprovalRevealTimerRef.current = null;
    }, DEMO_APPROVAL_CARD_DELAY_MS);

    return () => {
      clearDemoApprovalTimers();
    };
  }, [
    activeReportReady,
    clearDemoApprovalTimers,
    demoPlan,
    demoState,
    demoSummary,
    hasLiveSession,
  ]);

  const handleApproveDemoPlan = useCallback(() => {
    const runToken = demoRunTokenRef.current;
    clearDemoApprovalTimers();
    setDemoRejectEditorOpen(false);
    setIsSubmittingDemoApproval(true);
    demoApprovalSubmitTimerRef.current = window.setTimeout(() => {
      if (runToken !== demoRunTokenRef.current) {
        return;
      }
      setIsSubmittingDemoApproval(false);
      setDemoApprovalDecision("approved");
      setDemoApprovalCardOpen(false);
      demoApprovalSubmitTimerRef.current = null;
      playDemoApprovedRemediationFlow(runToken);
    }, DEMO_APPROVAL_SUBMIT_DELAY_MS);
  }, [clearDemoApprovalTimers, playDemoApprovedRemediationFlow]);
  const handleRejectDemoPlan = useCallback(() => {
    if (!demoApprovalReason.trim()) {
      return;
    }

    clearDemoApprovalTimers();
    setDemoRejectEditorOpen(false);
    setIsSubmittingDemoApproval(true);
    demoApprovalSubmitTimerRef.current = window.setTimeout(() => {
      setIsSubmittingDemoApproval(false);
      setDemoApprovalDecision("rejected");
      demoApprovalSubmitTimerRef.current = null;
    }, DEMO_APPROVAL_SUBMIT_DELAY_MS);
  }, [clearDemoApprovalTimers, demoApprovalReason]);

  useLayoutEffect(() => {
    autoScrollEnabledRef.current = true;
    scheduleFeedScrollToBottom();
  }, [activeSessionId, hasLiveSession, scheduleFeedScrollToBottom]);

  useLayoutEffect(() => {
    scheduleFeedScrollToBottom();
  }, [
    activePlan,
    activeSummary,
    activeTimeline,
    demoApprovalCardOpen,
    scheduleFeedScrollToBottom,
  ]);

  useEffect(() => {
    const feedElement = feedRef.current;
    if (!feedElement || typeof MutationObserver === "undefined") {
      return;
    }

    const observer = new MutationObserver(() => {
      scheduleFeedScrollToBottom();
    });

    observer.observe(feedElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    return () => observer.disconnect();
  }, [scheduleFeedScrollToBottom]);

  useEffect(
    () => () => {
      if (pendingAutoScrollFrameRef.current !== null) {
        window.cancelAnimationFrame(pendingAutoScrollFrameRef.current);
      }
    },
    [],
  );

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
    <div className="page-grid diagnosis-workspace-page">
      <section className="page-stage diagnosis-workspace-stage">
        <div className="page-stage__panel diagnosis-workspace-shell">
          <div className="diagnosis-workspace-shell__statusbar">
            <div className="diagnosis-workspace-shell__badges">
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

          <div className="diagnosis-workspace-shell__body">
            <div
              className={cn(
                "diagnosis-workspace-feed",
                approvalSurfaceOpen && "diagnosis-workspace-feed--with-approval",
              )}
              onScroll={handleFeedScroll}
              ref={feedRef}
            >
              <div className="diagnosis-workspace-feed__content">
                {activeTimeline.length === 0 ? (
                <div className="diagnosis-workspace-empty-state">
                  <div className="diagnosis-workspace-empty-state__icon">
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

                  if (item.kind === "run") {
                    return <ExecutionRunBlock item={item} key={item.id} />;
                  }

                  if (item.kind === "system") {
                    if (item.eventKind === "approval_result") {
                      return (
                        <ApprovalResultThoughtBlock item={item} key={item.id} />
                      );
                    }
                    return <SystemEventBlock item={item} key={item.id} />;
                  }

                  if (item.kind === "report") {
                    return <RCAReportCard item={item} key={item.id} />;
                  }

                  return <ToolCard item={item} key={item.id} />;
                })
              )}

              {hasLiveSession && traceStatus === "empty" ? (
                <div className="diagnosis-workspace-inline-note">
                  The live session has not produced trace entries yet. The input
                  remains available while waiting for incremental diagnosis
                  events.
                </div>
              ) : null}

              {inlineError ? (
                <div
                  className={cn(
                    "diagnosis-workspace-inline-note",
                    inlineError.tone === "error"
                      ? "diagnosis-workspace-inline-note--error"
                      : "diagnosis-workspace-inline-note--warning",
                  )}
                >
                  {inlineError.message}
                </div>
              ) : null}


              </div>
            </div>
            {approvalSurfaceOpen ? (
              <div className="diagnosis-workspace-approval-layer">
                {!hasLiveSession ? (
                  <DemoApprovalCard
                    isSubmitting={isSubmittingDemoApproval}
                    onApprove={handleApproveDemoPlan}
                    onCancelReject={() => {
                      setDemoApprovalReason("");
                      setDemoRejectEditorOpen(false);
                    }}
                    onExpandReject={() => setDemoRejectEditorOpen(true)}
                    onReject={handleRejectDemoPlan}
                    onRejectReasonChange={setDemoApprovalReason}
                    open={demoApprovalCardOpen}
                    plan={activePlan}
                    rejectExpanded={demoRejectEditorOpen}
                    rejectReason={demoApprovalReason}
                    resolution={demoApprovalDecision}
                    summary={activeSummary}
                  />
                ) : null}
                <ApprovalOverlay
                  approvalBlockReason={approvalBlockReason}
                  canApprove={canApprove}
                  isSubmitting={isApprovingPlan}
                  onApprove={() => void handleApprovePlan()}
                  onCancelReject={() => {
                    setApprovalReason("");
                    setApprovalRejectEditorOpen(false);
                  }}
                  onExpandReject={() => setApprovalRejectEditorOpen(true)}
                  onReject={() => void handleRejectPlan()}
                  onRejectReasonChange={setApprovalReason}
                  open={hasLiveSession && approvalOverlayOpen}
                  plan={activePlan}
                  planVersion={latestPlanVersion}
                  rejectExpanded={approvalRejectEditorOpen}
                  rejectReason={approvalReason}
                  statusLabel={activeApprovalStatusLabel}
                  statusTone={getDiagnosisStatusBadgeTone(session?.status)}
                  summary={activeSummary}
                />
              </div>
            ) : null}
          </div>

          <footer className="diagnosis-workspace-shell__footer">
            <div className="diagnosis-workspace-composer-anchor">
              <div className="diagnosis-workspace-composer">
                <textarea
                  className="diagnosis-workspace-composer__input"
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
                <div className="diagnosis-workspace-composer__actions">
                  <p className="diagnosis-workspace-composer__hint">
                    {hasLiveSession
                      ? "The live data stream is preserved and rendered with Toolcall pacing and hierarchy."
                      : "Without an active session, local demo mode runs and replays a slower Toolcall-style diagnosis flow."}
                  </p>
                  <div className="diagnosis-workspace-composer__buttons">
                    {!hasLiveSession && demoTimeline.length > 0 ? (
                      <button
                        className="diagnosis-workspace-send-btn diagnosis-workspace-send-btn--secondary"
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
                      className="diagnosis-workspace-send-btn"
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
            <p className="diagnosis-workspace-shell__footer-note">
              Agent can make mistakes. Consider verifying important information.
            </p>
          </footer>
        </div>
      </section>
    </div>
  );
}
export default DiagnosisPage;
