import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useParams } from "react-router-dom";

import { buildBackendWsUrl } from "../api/ws";
import { AppIcon, SectionHeader } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";
import { formatDateTimeParts, formatTimestamp } from "../utils/format";
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
type ApprovalState = "pending" | "modifying" | "updating" | "approved" | "rejected";
type ApprovalPlanCardProps = {
  plan: DiagnosisModifiedPlanView;
  mode: "demo" | "live";
  currentStatus?: string;
  planVersion?: number | null;
  canApprove?: boolean;
  approvalBlockReason?: string;
  isApproving?: boolean;
  isRevising?: boolean;
  errorMessage?: string;
  onApprove?: () => void;
  onReject?: () => void;
  onRevise?: (instruction: string) => void;
};

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
  return `已思考 ${safeDuration} 秒`;
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
      message: "未找到对应会话（404）。该会话可能已过期，或后端详情接口暂不可用。你仍然可以在下方输入新的诊断请求。",
    };
  }
  return { tone: "error", message: error };
}

function getConnectionStateLabel(state: "connecting" | "open" | "closed" | "error") {
  switch (state) {
    case "connecting":
      return "连接中";
    case "open":
      return "已连接";
    case "closed":
      return "已断开";
    case "error":
      return "连接异常";
    default:
      return state;
  }
}

function getToolStatusLabel(status: "loading" | "success" | "error" | "timeout") {
  switch (status) {
    case "loading":
      return "正在查询";
    case "success":
      return "查询完成";
    case "error":
      return "查询失败";
    case "timeout":
      return "查询超时";
    default:
      return status;
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

function ToneBadge({ children, tone = "neutral" }: { children: ReactNode; tone?: BadgeTone }) {
  return <span className={cn("diagnosis-modified-badge", `diagnosis-modified-badge--${tone}`)}>{children}</span>;
}

function getApprovalStatusTone(status: string | undefined): BadgeTone {
  const normalized = String(status ?? "").trim().toLowerCase();
  if (normalized === "approval_required" || normalized === "awaiting_approval") {
    return "accent";
  }
  if (normalized === "remediating" || normalized === "validating") {
    return "warning";
  }
  if (normalized === "resolved") {
    return "success";
  }
  if (normalized === "failed" || normalized === "timeout" || normalized === "escalated" || normalized === "rejected") {
    return "danger";
  }
  return "neutral";
}

function getApprovalStatusLabel(status: string | undefined): string {
  const normalized = String(status ?? "").trim().toLowerCase();
  if (normalized === "approval_required" || normalized === "awaiting_approval") {
    return "待审批";
  }
  if (normalized === "remediating") {
    return "执行中";
  }
  if (normalized === "resolved") {
    return "已恢复";
  }
  if (normalized === "failed") {
    return "执行失败";
  }
  if (normalized === "timeout") {
    return "执行超时";
  }
  if (normalized === "escalated") {
    return "待人工介入";
  }
  if (normalized === "rejected") {
    return "已拒绝";
  }
  return status?.trim() || "未知状态";
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
    <article className={cn("diagnosis-modified-message-row", isUser && "diagnosis-modified-message-row--user")} title={hoverTime}>
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
    </article>
  );
}
function ThinkingBlock({
  item,
  animate,
  autoCollapseOnComplete,
  onStreamComplete,
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }>;
  animate: boolean;
  autoCollapseOnComplete: boolean;
  onStreamComplete?: () => void;
}) {
  const isThinking = item.status === "thinking";
  const [isExpanded, setIsExpanded] = useState(true);

  useEffect(() => {
    if (isThinking) {
      setIsExpanded(true);
      return;
    }

    if (!autoCollapseOnComplete) {
      return;
    }

    const timer = window.setTimeout(() => setIsExpanded(false), DEMO_THINKING_COLLAPSE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [autoCollapseOnComplete, isThinking, item.id]);

  return (
    <article className="diagnosis-modified-process-row">
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
              {isThinking ? "思考中..." : formatThoughtDurationLabel(item.thoughtDurationSec)}
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
    </article>
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
    <article className="diagnosis-modified-process-row diagnosis-modified-process-row--tool">
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
            <span className="diagnosis-modified-tool-card__status-label">{getToolStatusLabel(item.status)}</span>
          </div>

          {isExpanded ? (
            <div className="diagnosis-modified-tool-card__body">
              {item.status === "loading" ? (
                <div className="diagnosis-modified-tool-card__loading">
                  <div>
                    <span className="diagnosis-modified-tool-card__loading-indicator" />
                    <span>正在连接 telemetry 数据流...</span>
                  </div>
                  <div>
                    <span className="diagnosis-modified-tool-card__loading-indicator" />
                    <span>正在查询诊断上下文...</span>
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
    </article>
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
            <ToneBadge tone="neutral">{"\u6839\u56e0\u8bca\u65ad"}</ToneBadge>
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
          <span>{"\u786e\u5b9a\u6027"}</span>
          <strong>{summary.certaintyLabel}</strong>
        </div>
        <div>
          <span>{"\u7f6e\u4fe1\u5ea6"}</span>
          <strong>{summary.confidenceRawLabel ?? summary.confidenceLabel}</strong>
        </div>
        <div>
          <span>{"\u4f18\u5148\u7ea7"}</span>
          <strong>{summary.priorityLabel ?? "--"}</strong>
        </div>
        <div>
          <span>{"\u66f4\u65b0\u65f6\u95f4"}</span>
          <strong>{lastTimestamp ? lastParts.time : "--"}</strong>
        </div>
      </div>

      <div className="diagnosis-modified-report-card__sections">
        <div>
          <p className="diagnosis-modified-report-card__section-label">{"\u5f53\u524d\u7ed3\u8bba"}</p>
          <div className="diagnosis-modified-report-card__facts">
            <p>
              <span>{"\u6839\u56e0\uff1a"}</span>
              {summary.rootCause ?? primaryCandidate?.title ?? "--"}
            </p>
            <p>
              <span>{"\u5c42\u7ea7\uff1a"}</span>
              {summary.rootCauseLayerLabel ?? summary.rootCauseLayer ?? primaryCandidate?.layer ?? "--"}
            </p>
            <p>
              <span>{"\u5b9e\u4f53\uff1a"}</span>
              {summary.rootCauseEntities && summary.rootCauseEntities.length > 0
                ? summary.rootCauseEntities.join("\uff0c")
                : primaryCandidate?.entities?.join("\uff0c") || "--"}
            </p>
            <p>
              <span>{"\u5f71\u54cd\uff1a"}</span>
              {summary.impactSummary}
            </p>
            <p>
              <span>{"\u53d7\u5f71\u54cd\u670d\u52a1\uff1a"}</span>
              {summary.affectedServices.length > 0 ? summary.affectedServices.join("\uff0c") : "--"}
            </p>
          </div>
        </div>

        <div>
          <p className="diagnosis-modified-report-card__section-label">{`\u5019\u9009\u6839\u56e0\uff08${candidates.length}\uff09`}</p>
          <div className="diagnosis-modified-report-card__candidates">
            {candidates.length > 0 ? (
              candidates.map((candidate, index) => (
                <div key={candidate.id} className="diagnosis-modified-report-card__candidate-item">
                  <p>
                    #{candidate.rank ?? index + 1} {candidate.title} ({candidate.confidence.toFixed(2)}) {"\u8bc1\u636e\u6458\u8981\uff1a"}
                    {candidate.evidenceSummary ?? candidate.summary}
                  </p>
                  {candidate.distinguishingVerification ? <p>{"\u533a\u5206\u9a8c\u8bc1\uff1a"}{candidate.distinguishingVerification}</p> : null}
                </div>
              ))
            ) : (
              <p className="diagnosis-modified-report-card__section-copy">{"\u6682\u65e0\u5019\u9009\u6839\u56e0\u3002"}</p>
            )}
          </div>
        </div>

        <div>
          <p className="diagnosis-modified-report-card__section-label">{"\u5047\u8bbe\u4e0e\u8bc1\u636e"}</p>
          <div className="diagnosis-modified-report-card__hypotheses">
            {hypothesisRows.length > 0 ? (
              hypothesisRows.map((item, index) => (
                <p key={item.id}>
                  {String.fromCharCode(65 + index)}. {item.description} [{item.statusLabel}] {"\u652f\u6301\u8bc1\u636e"}({item.evidenceForCount}) {"\u53cd\u8bc1"}({item.evidenceAgainstCount})
                </p>
              ))
            ) : (
              <p className="diagnosis-modified-report-card__section-copy">{"\u6682\u65e0\u5047\u8bbe\u8bc1\u636e\u6570\u636e\u3002"}</p>
            )}
          </div>
        </div>

        <div>
          <details className="diagnosis-modified-report-card__propagation">
            <summary className="diagnosis-modified-report-card__section-label">{"\u4f20\u64ad\u94fe\u8def\uff08\u6298\u53e0\uff09"}</summary>
            <div className="diagnosis-modified-report-card__propagation-body">
              {chainRows.length > 0 ? (
                chainRows.map((step) => (
                  <p key={step.id}>
                    {step.entityId} -&gt; {step.metric} -&gt; {step.valueBefore} -&gt; {step.valueAfter} -&gt; {step.description}
                  </p>
                ))
              ) : (
                <p className="diagnosis-modified-report-card__section-copy">{"\u6682\u65e0\u4f20\u64ad\u94fe\u8def\u6570\u636e\u3002"}</p>
              )}
            </div>
          </details>
        </div>
      </div>
    </section>
  );
}
function ApprovalPlanCard({
  plan,
  mode,
  currentStatus,
  planVersion,
  canApprove = false,
  approvalBlockReason,
  isApproving = false,
  isRevising = false,
  errorMessage,
  onApprove,
  onReject,
  onRevise,
}: ApprovalPlanCardProps) {
  const isLive = mode === "live";
  const [status, setStatus] = useState<ApprovalState>("pending");
  const [modifyInput, setModifyInput] = useState("");
  const [actions, setActions] = useState(plan.steps.map((step) => step.title));

  useEffect(() => {
    setStatus("pending");
    setModifyInput("");
    setActions(plan.steps.map((step) => step.title));
  }, [mode, plan]);

  const handleSubmitModify = useCallback(() => {
    if (isLive) {
      onRevise?.(modifyInput.trim());
      setModifyInput("");
      setStatus("pending");
      return;
    }

    if (!modifyInput.trim()) {
      return;
    }

    setStatus("updating");
    window.setTimeout(() => {
      setActions((current) =>
        current.map((action, index) => {
          if (index === 1) {
            return "先仅对 canary 实例放量，再观察 15 分钟 Redis timeout 与 error rate，确认稳定后再扩大范围。";
          }
          if (index === 2) {
            return "进入全量前，再补一次 database wait queue 与 cache hit ratio 的校验。";
          }
          return action;
        }),
      );
      setModifyInput("");
      setStatus("pending");
    }, 1600);
  }, [isLive, modifyInput, onRevise]);

  const liveStatusTone = getApprovalStatusTone(currentStatus);
  const liveStatusLabel = getApprovalStatusLabel(currentStatus);
  const liveStatusText = currentStatus?.trim() || "unknown";
  const showDemoResolvedState = status === "approved" || status === "rejected";

  if (isLive) {
    return (
      <section className="diagnosis-modified-approval-card">
        <header className="diagnosis-modified-approval-card__header">
          <div>
            <div className="diagnosis-modified-approval-card__eyebrow">
              <ToneBadge tone="neutral">执行计划</ToneBadge>
              <ToneBadge tone={liveStatusTone}>{liveStatusLabel}</ToneBadge>
            </div>
            <h3 className="diagnosis-modified-approval-card__title">{plan.title}</h3>
            <p className="diagnosis-modified-approval-card__description">{plan.description}</p>
          </div>
        </header>

        <div className="diagnosis-modified-approval-card__body">
          <div className="diagnosis-modified-approval-card__meta-row">
            <ToneBadge tone="warning">{plan.priorityLabel}</ToneBadge>
            <ToneBadge tone="neutral">{`AI 置信度 ${plan.confidenceLabel}`}</ToneBadge>
            {plan.safetyLabel ? <ToneBadge tone="info">{plan.safetyLabel}</ToneBadge> : null}
            {plan.canaryLabel ? <ToneBadge tone="neutral">{plan.canaryLabel}</ToneBadge> : null}
            {planVersion ? <ToneBadge tone="neutral">{`方案 v${planVersion}`}</ToneBadge> : null}
          </div>

          <div className="diagnosis-modified-inline-note">
            当前状态：{liveStatusText}。只有 `approval_required` 且方案版本最新时，审批按钮才会触发真实后端执行。
          </div>
          {approvalBlockReason ? (
            <div className="diagnosis-modified-inline-note diagnosis-modified-inline-note--warning">{approvalBlockReason}</div>
          ) : null}
          {errorMessage ? (
            <div className="diagnosis-modified-inline-note diagnosis-modified-inline-note--error">{errorMessage}</div>
          ) : null}

          <div className="diagnosis-modified-approval-card__actions-wrap">
            {isApproving ? (
              <div className="diagnosis-modified-approval-card__updating">正在提交审批并等待后端回填执行状态...</div>
            ) : null}
            {isRevising ? <div className="diagnosis-modified-approval-card__updating">正在更新方案...</div> : null}
            <ol className="diagnosis-modified-approval-card__actions">
              {plan.steps.map((step, index) => (
                <li key={`${step.id}-${index}`}>
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

          <div className="diagnosis-modified-approval-card__footer">
            <button
              className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary"
              disabled={!canApprove || isApproving || isRevising}
              onClick={() => onApprove?.()}
              type="button"
            >
              审批通过
            </button>
            <button
              className="diagnosis-modified-action-btn"
              disabled={!canApprove || isApproving || isRevising}
              onClick={() => onReject?.()}
              type="button"
            >
              拒绝
            </button>
            <button
              className="diagnosis-modified-action-btn"
              disabled={isApproving || isRevising}
              onClick={() => setStatus("modifying")}
              type="button"
            >
              修改方案
            </button>
          </div>

          {status === "modifying" ? (
            <div className="diagnosis-modified-approval-card__editor">
              <p>补充给 Agent 的修改意见；留空时会使用默认优化指令。</p>
              <div className="diagnosis-modified-approval-card__editor-row">
                <textarea
                  disabled={isApproving || isRevising}
                  onChange={(event) => setModifyInput(event.target.value)}
                  placeholder="例如：避免一次性全量发布，先从 canary 验证开始。"
                  value={modifyInput}
                />
                <div>
                  <button
                    className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary"
                    disabled={isApproving || isRevising}
                    onClick={handleSubmitModify}
                    type="button"
                  >
                    更新方案
                  </button>
                  <button
                    className="diagnosis-modified-action-btn"
                    disabled={isApproving || isRevising}
                    onClick={() => setStatus("pending")}
                    type="button"
                  >
                    取消
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    );
  }

  return (
    <section
      className={cn(
        "diagnosis-modified-approval-card",
        status === "approved" && "diagnosis-modified-approval-card--approved",
        status === "rejected" && "diagnosis-modified-approval-card--rejected",
      )}
    >
      <header className="diagnosis-modified-approval-card__header">
        <div>
          <div className="diagnosis-modified-approval-card__eyebrow">
            <ToneBadge tone="neutral">执行计划</ToneBadge>
            {status === "approved" ? (
              <ToneBadge tone="success">已批准</ToneBadge>
            ) : status === "rejected" ? (
              <ToneBadge tone="danger">已拒绝</ToneBadge>
            ) : (
              <ToneBadge tone="accent">待审批</ToneBadge>
            )}
          </div>
          <h3 className="diagnosis-modified-approval-card__title">{plan.title}</h3>
          <p className="diagnosis-modified-approval-card__description">{plan.description}</p>
        </div>
      </header>

      <div className="diagnosis-modified-approval-card__body">
        <div className="diagnosis-modified-approval-card__meta-row">
          <ToneBadge tone="warning">{plan.priorityLabel}</ToneBadge>
          <ToneBadge tone="neutral">{`AI 置信度 ${plan.confidenceLabel}`}</ToneBadge>
          {plan.safetyLabel ? <ToneBadge tone="info">{plan.safetyLabel}</ToneBadge> : null}
          {plan.canaryLabel ? <ToneBadge tone="neutral">{plan.canaryLabel}</ToneBadge> : null}
        </div>

        <div className="diagnosis-modified-inline-note diagnosis-modified-inline-note--warning">
          当前为演示模式，审批按钮只会更新前端演示状态，不会触发真实修复执行。
        </div>

        <div className="diagnosis-modified-approval-card__actions-wrap">
          {status === "updating" ? <div className="diagnosis-modified-approval-card__updating">正在更新方案...</div> : null}
          <ol className="diagnosis-modified-approval-card__actions">
            {actions.map((action, index) => (
              <li key={`${action}-${index}`}>
                <span>{index + 1}</span>
                <div>
                  <strong>{action}</strong>
                  {plan.steps[index]?.detail ? <p>{plan.steps[index]?.detail}</p> : null}
                  {plan.steps[index]?.paramsSummary ? <code>{plan.steps[index]?.paramsSummary}</code> : null}
                </div>
              </li>
            ))}
          </ol>
        </div>

        {status === "pending" ? (
          <div className="diagnosis-modified-approval-card__footer">
            <button className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary" onClick={() => setStatus("approved")} type="button">
              审批通过
            </button>
            <button className="diagnosis-modified-action-btn" onClick={() => setStatus("rejected")} type="button">
              拒绝
            </button>
            <button className="diagnosis-modified-action-btn" onClick={() => setStatus("modifying")} type="button">
              修改方案
            </button>
          </div>
        ) : null}

        {status === "modifying" ? (
          <div className="diagnosis-modified-approval-card__editor">
            <p>补充给 Agent 的修改意见。</p>
            <div className="diagnosis-modified-approval-card__editor-row">
              <textarea
                onChange={(event) => setModifyInput(event.target.value)}
                placeholder="例如：避免一次性全量发布，先从 canary 验证开始。"
                value={modifyInput}
              />
              <div>
                <button
                  className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary"
                  disabled={!modifyInput.trim()}
                  onClick={handleSubmitModify}
                  type="button"
                >
                  更新方案
                </button>
                <button className="diagnosis-modified-action-btn" onClick={() => setStatus("pending")} type="button">
                  取消
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {showDemoResolvedState ? (
          <div className="diagnosis-modified-approval-card__resolved">
            <p>{status === "approved" ? "方案已批准，Agent 将按计划执行后续动作。" : "方案已拒绝，Agent 将等待进一步指令。"}</p>
          </div>
        ) : null}
      </div>
    </section>
  );

  return (
    <section
      className={cn(
        "diagnosis-modified-approval-card",
        !isLive && status === "approved" && "diagnosis-modified-approval-card--approved",
        !isLive && status === "rejected" && "diagnosis-modified-approval-card--rejected",
      )}
    >
      <header className="diagnosis-modified-approval-card__header">
        <div>
          <div className="diagnosis-modified-approval-card__eyebrow">
            <ToneBadge tone="neutral">执行计划</ToneBadge>
            {status === "approved" ? (
              <ToneBadge tone="success">已批准</ToneBadge>
            ) : status === "rejected" ? (
              <ToneBadge tone="danger">已拒绝</ToneBadge>
            ) : (
              <ToneBadge tone="accent">待审批</ToneBadge>
            )}
          </div>
          <h3 className="diagnosis-modified-approval-card__title">{plan.title}</h3>
          <p className="diagnosis-modified-approval-card__description">{plan.description}</p>
        </div>
      </header>

      <div className="diagnosis-modified-approval-card__body">
        <div className="diagnosis-modified-approval-card__meta-row">
          <ToneBadge tone="warning">{plan.priorityLabel}</ToneBadge>
          <ToneBadge tone="neutral">{`AI 置信度 ${plan.confidenceLabel}`}</ToneBadge>
          {plan.safetyLabel ? <ToneBadge tone="info">{plan.safetyLabel}</ToneBadge> : null}
          {plan.canaryLabel ? <ToneBadge tone="neutral">{plan.canaryLabel}</ToneBadge> : null}
        </div>

        <div className="diagnosis-modified-approval-card__actions-wrap">
          {status === "updating" ? <div className="diagnosis-modified-approval-card__updating">正在更新方案...</div> : null}
          <ol className="diagnosis-modified-approval-card__actions">
            {actions.map((action, index) => (
              <li key={`${action}-${index}`}>
                <span>{index + 1}</span>
                <div>
                  <strong>{action}</strong>
                  {plan.steps[index]?.detail ? <p>{plan.steps[index]?.detail}</p> : null}
                  {plan.steps[index]?.paramsSummary ? <code>{plan.steps[index]?.paramsSummary}</code> : null}
                </div>
              </li>
            ))}
          </ol>
        </div>

        {status === "pending" ? (
          <div className="diagnosis-modified-approval-card__footer">
            <button className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary" onClick={() => setStatus("approved")} type="button">
              批准并执行
            </button>
            <button className="diagnosis-modified-action-btn" onClick={() => setStatus("rejected")} type="button">
              拒绝
            </button>
            <button className="diagnosis-modified-action-btn" onClick={() => setStatus("modifying")} type="button">
              修改方案
            </button>
          </div>
        ) : null}

        {status === "modifying" ? (
          <div className="diagnosis-modified-approval-card__editor">
            <p>补充给 Agent 的修改意见</p>
            <div className="diagnosis-modified-approval-card__editor-row">
              <textarea
                onChange={(event) => setModifyInput(event.target.value)}
                placeholder="例如：避免一次性全量发布，先从 canary 验证开始。"
                value={modifyInput}
              />
              <div>
                <button
                  className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary"
                  disabled={!modifyInput.trim()}
                  onClick={handleSubmitModify}
                  type="button"
                >
                  更新方案
                </button>
                <button className="diagnosis-modified-action-btn" onClick={() => setStatus("pending")} type="button">
                  取消
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {(status === "approved" || status === "rejected") ? (
          <div className="diagnosis-modified-approval-card__resolved">
            <p>{status === "approved" ? "方案已批准，Agent 将按计划执行后续动作。" : "方案已拒绝，Agent 将等待进一步指令。"}</p>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function DiagnosisModifiedPage() {
  const params = useParams<{ sessionId?: string }>();
  const routeSessionId = (params.sessionId ?? "").trim();
  const shouldBootstrapLiveSession = routeSessionId.length > 0;
  const [draft, setDraft] = useState("");
  const [demoTimeline, setDemoTimeline] = useState<DiagnosisModifiedTimelineItem[]>([]);
  const [demoCandidates, setDemoCandidates] = useState<DiagnosisModifiedCandidateView[]>([]);
  const [demoSummary, setDemoSummary] = useState<DiagnosisModifiedSummaryView | undefined>();
  const [demoPlan, setDemoPlan] = useState<DiagnosisModifiedPlanView | undefined>();
  const [demoHypotheses, setDemoHypotheses] = useState<DiagnosisModifiedHypothesisView[]>([]);
  const [demoPropagationChain, setDemoPropagationChain] = useState<DiagnosisModifiedPropagationStepView[]>([]);
  const [demoState, setDemoState] = useState<"idle" | "running" | "complete">("idle");
  const [lastDemoPrompt, setLastDemoPrompt] = useState("");
  const [activeStreamingMessageId, setActiveStreamingMessageId] = useState<string | null>(null);
  const [liveTimeline, setLiveTimeline] = useState<DiagnosisModifiedTimelineItem[]>([]);

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
    bootstrapStatus,
    traceStatus,
    isLoadingSession,
    isSendingMessage,
    connectionState,
    error,
    bootstrapSession,
    sendMessage,
    revisePlan,
    approvePlan,
    applyEvent,
    setConnectionState,
    canApprove,
    approvalBlockReason,
    isApprovingPlan,
    isRevisingPlan,
    latestPlanVersion,
    liveThinking,
    isStreamingDiagnosis,
    cancelStreamingDiagnosis,
  } = useDiagnosisStore();

  const liveView = useMemo(() => buildDiagnosisModifiedLiveView(session, messages, liveThinking), [liveThinking, messages, session]);
  const hasLiveSession =
    shouldBootstrapLiveSession && bootstrapStatus === "ready" && Boolean(session) && Boolean(activeSessionId);

  useEffect(() => {
    if (!shouldBootstrapLiveSession) {
      return;
    }
    void bootstrapSession(routeSessionId);
  }, [bootstrapSession, routeSessionId, shouldBootstrapLiveSession]);

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

  useEffect(() => {
    return () => {
      if (isStreamingDiagnosis) {
        cancelStreamingDiagnosis();
      }
    };
  }, []);

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
  const activeCandidates = hasLiveSession ? liveView.candidates : demoCandidates;
  const activeHypotheses = hasLiveSession ? liveView.hypotheses ?? [] : demoHypotheses;
  const activePropagationChain = hasLiveSession ? liveView.propagationChain ?? [] : demoPropagationChain;
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
    (shouldBootstrapLiveSession ? isLoadingSession : false) || isSendingMessage || demoState === "running";
  const introCopy = hasLiveSession
    ? "\u5f53\u524d\u9875\u9762\u6b63\u5728\u6d88\u8d39\u771f\u5b9e\u8bca\u65ad\u4f1a\u8bdd\uff0c\u5e76\u6309\u7edf\u4e00\u6d41\u7a0b\u5448\u73b0\u601d\u8003\u3001\u5de5\u5177\u8c03\u7528\u3001\u6839\u56e0\u5206\u6790\u4e0e\u5ba1\u6279\u6267\u884c\u3002"
    : demoState === "idle"
      ? "\u8f93\u5165\u8bca\u65ad\u95ee\u9898\u540e\uff0c\u9875\u9762\u4f1a\u6309\u8f83\u6162\u8282\u594f\u56de\u653e\u5b8c\u6574\u8bca\u65ad\u8fc7\u7a0b\uff0c\u4fbf\u4e8e\u9010\u6b65\u67e5\u770b\u6bcf\u4e00\u6b21\u601d\u8003\u4e0e\u5de5\u5177\u8c03\u7528\u3002"
      : "\u5f53\u524d\u6b63\u5728\u6309\u6162\u901f\u56de\u653e\u8bca\u65ad\u6d41\u7a0b\u3002";

  return (
    <div className="page-grid diagnosis-modified-page">
      <div className="page-intro">
        <SectionHeader
          title="诊断（修改）"
          description={introCopy}
          actions={
            <div className="diagnosis-modified-shell__header-actions">
              <div className="diagnosis-modified-shell__badges">
                <ToneBadge tone={hasLiveSession ? "success" : "accent"}>{hasLiveSession ? "\u5b9e\u65f6\u4f1a\u8bdd" : "\u6f14\u793a\u6a21\u5f0f"}</ToneBadge>
                {hasLiveSession && activeSessionId ? <ToneBadge tone="neutral">{activeSessionId}</ToneBadge> : null}
                {hasLiveSession ? <ToneBadge tone={connectionState === "open" ? "success" : "warning"}>{`实时链路 ${getConnectionStateLabel(connectionState)}`}</ToneBadge> : null}
              </div>
            </div>
          }
        />
      </div>

      <section className="diagnosis-modified-shell">
        <div className="diagnosis-modified-shell__body">
          <div className="diagnosis-modified-feed">
            {activeTimeline.length === 0 ? (
              <div className="diagnosis-modified-empty-state">
                <div className="diagnosis-modified-empty-state__icon">
                  <AppIcon name="aiChat" size={18} />
                </div>
                <h2>{"\u6b22\u8fce\u4f7f\u7528 RCA Agent"}</h2>
                <p>{"\u5728\u4e0b\u65b9\u8f93\u5165\u8bca\u65ad\u8bf7\u6c42\u5373\u53ef\u89e6\u53d1 ReAct \u8bca\u65ad\u6d41\u7a0b\uff0c\u6216\u76f4\u63a5\u4f7f\u7528\u9884\u7f6e\u793a\u4f8b\u5f00\u59cb\u56de\u653e\u3002"}</p>
              </div>
            ) : (
              activeTimeline.map((item) => {
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
                      autoCollapseOnComplete={!hasLiveSession}
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
              })
            )}

            {hasLiveSession && traceStatus === "empty" && !liveThinking ? (
              <div className="diagnosis-modified-inline-note">
                当前实时会话还没有产出 trace 条目。等待增量诊断事件期间，输入框仍可继续使用。
              </div>
            ) : null}

            {inlineError ? (
              <div
                className={cn(
                  "diagnosis-modified-inline-note",
                  inlineError.tone === "error" ? "diagnosis-modified-inline-note--error" : "diagnosis-modified-inline-note--warning",
                )}
              >
                {inlineError.message}
              </div>
            ) : null}

            {activeSummary ? <RCAReportCard candidates={activeCandidates} hypotheses={activeHypotheses} propagationChain={activePropagationChain} summary={activeSummary} timeline={activeTimeline} /> : null}
            {activePlan ? (
              <ApprovalPlanCard
                approvalBlockReason={hasLiveSession ? approvalBlockReason : undefined}
                canApprove={hasLiveSession ? canApprove : undefined}
                currentStatus={hasLiveSession ? session?.status : undefined}
                errorMessage={hasLiveSession ? error : undefined}
                isApproving={hasLiveSession ? isApprovingPlan : undefined}
                isRevising={hasLiveSession ? isRevisingPlan : undefined}
                mode={hasLiveSession ? "live" : "demo"}
                onApprove={hasLiveSession ? () => void approvePlan(true) : undefined}
                onReject={hasLiveSession ? () => void approvePlan(false) : undefined}
                onRevise={hasLiveSession ? (instruction) => void revisePlan(instruction) : undefined}
                plan={activePlan}
                planVersion={hasLiveSession ? latestPlanVersion : undefined}
              />
            ) : null}
            <div ref={bottomRef} />
          </div>
        </div>

        <footer className="diagnosis-modified-shell__footer">
          <div className="diagnosis-modified-composer">
            <textarea
              className="diagnosis-modified-composer__input"
              disabled={composerDisabled}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder={
                hasLiveSession
                  ? "继续当前诊断会话，例如：解释为什么会选择这些候选根因。"
                  : "请输入诊断问题，例如：分析 auth-svc 最近一小时的时延与错误率抖动。（按 Enter 开始）"
              }
              rows={1}
              value={draft}
            />
            <div className="diagnosis-modified-composer__actions">
              <p className="diagnosis-modified-composer__hint">
                {hasLiveSession
                  ? "\u5b9e\u65f6\u6570\u636e\u6d41\u4f1a\u88ab\u4fdd\u7559\uff0c\u5e76\u6309 Toolcall \u7684\u8282\u594f\u4e0e\u5c42\u7ea7\u8fdb\u884c\u6e32\u67d3\u3002"
                  : "\u6ca1\u6709\u6d3b\u52a8\u4f1a\u8bdd\u65f6\uff0c\u5c06\u8fd0\u884c\u672c\u5730\u6f14\u793a\u6a21\u5f0f\uff0c\u5e76\u4ee5\u8f83\u6162\u8282\u594f\u56de\u653e Toolcall \u98ce\u683c\u7684\u8bca\u65ad\u6d41\u7a0b\u3002"}
              </p>
              <div className="diagnosis-modified-composer__buttons">
                {!hasLiveSession && demoTimeline.length > 0 ? (
                  <button className="diagnosis-modified-send-btn diagnosis-modified-send-btn--secondary" onClick={() => startDemo(lastDemoPrompt || draft || "分析 auth-svc 过去一小时的时延与错误率抖动")} type="button">
                    重新回放
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
          <p className="diagnosis-modified-shell__footer-note">Agent can make mistakes. Consider verifying important information.</p>
        </footer>
      </section>
    </div>
  );
}
export default DiagnosisModifiedPage;
