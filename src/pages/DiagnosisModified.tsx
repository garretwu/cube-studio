import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useParams } from "react-router-dom";

import { buildBackendWsUrl } from "../api/ws";
import { AppIcon } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";
import { formatDateTimeParts, formatTimestamp } from "../utils/format";
import {
  buildDiagnosisModifiedDemoScenario,
  buildDiagnosisModifiedLiveView,
  type DiagnosisModifiedCandidateView,
  type DiagnosisModifiedDemoEvent,
  type DiagnosisModifiedPlanView,
  type DiagnosisModifiedSummaryView,
  type DiagnosisModifiedTimelineItem,
} from "./diagnosisModifiedModel";

type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";
type ApprovalState = "pending" | "modifying" | "updating" | "approved" | "rejected";

const DEMO_TEXT_SPEED_MS = 28;
const DEMO_THINKING_COLLAPSE_DELAY_MS = 1600;
const DEMO_TOOL_COLLAPSE_DELAY_MS = 960;
const DEMO_EVENT_SLOWDOWN = 1.9;

function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

function resolveInlineError(error?: string): { tone: "error" | "warning"; message: string } | null {
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

function useProgressiveText(text: string, animate: boolean, speedMs = DEMO_TEXT_SPEED_MS) {
  const [displayedText, setDisplayedText] = useState(animate ? "" : text);

  useEffect(() => {
    if (!animate) {
      setDisplayedText(text);
      return;
    }

    setDisplayedText("");
    let index = 0;
    const timer = window.setInterval(() => {
      index += 1;
      setDisplayedText(text.slice(0, index));
      if (index >= text.length) {
        window.clearInterval(timer);
      }
    }, speedMs);

    return () => window.clearInterval(timer);
  }, [animate, speedMs, text]);

  return displayedText;
}

function ToneBadge({ children, tone = "neutral" }: { children: ReactNode; tone?: BadgeTone }) {
  return <span className={cn("diagnosis-modified-badge", `diagnosis-modified-badge--${tone}`)}>{children}</span>;
}

function StreamingText({ text, animate = false, className }: { text: string; animate?: boolean; className?: string }) {
  const displayedText = useProgressiveText(text, animate);
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
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "message" }>;
  animate: boolean;
}) {
  const isUser = item.role === "user";

  return (
    <article className={cn("diagnosis-modified-message-row", isUser && "diagnosis-modified-message-row--user")}>
      <div className={cn("diagnosis-modified-avatar", isUser ? "diagnosis-modified-avatar--user" : "diagnosis-modified-avatar--assistant")}>
        <AppIcon name={isUser ? "users3" : "spark"} size={14} />
      </div>
      <div className="diagnosis-modified-message-row__body">
        <div className="diagnosis-modified-message-row__meta">
          <span>{item.label ?? (isUser ? "Your input" : "Agent response")}</span>
          <span>{formatTimestamp(item.timestamp)}</span>
        </div>
        <p className="diagnosis-modified-message-row__text">
          <StreamingText animate={animate && !isUser} text={item.content} />
        </p>
      </div>
    </article>
  );
}

function ThinkingBlock({
  item,
  animate,
}: {
  item: Extract<DiagnosisModifiedTimelineItem, { kind: "thinking" }>;
  animate: boolean;
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

  return (
    <article className="diagnosis-modified-process-row">
      <div className="diagnosis-modified-avatar diagnosis-modified-avatar--assistant">
        <AppIcon name="spark" size={14} />
      </div>
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
          <span className="diagnosis-modified-thinking__label">{isThinking ? "Thinking..." : item.title}</span>
          {item.toolName ? <span className="diagnosis-modified-thinking__tool">{item.toolName}</span> : null}
        </button>

        {isExpanded ? (
          <div className="diagnosis-modified-thinking__panel">
            <div className="diagnosis-modified-thinking__content">
              <p>
                <StreamingText animate={animate && isThinking} text={item.content} />
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

    const timer = window.setTimeout(() => setIsExpanded(false), DEMO_TOOL_COLLAPSE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [item.id, item.status]);

  return (
    <article className="diagnosis-modified-process-row diagnosis-modified-process-row--tool">
      <div className="diagnosis-modified-avatar diagnosis-modified-avatar--assistant">
        <AppIcon name="spark" size={14} />
      </div>
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
              <span className={cn("diagnosis-modified-tool-card__status-dot", `diagnosis-modified-tool-card__status-dot--${item.status}`)} />
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
                    <span className="diagnosis-modified-tool-card__loading-dot" />
                    <span>Connecting to telemetry stream...</span>
                  </div>
                  <div>
                    <span className="diagnosis-modified-tool-card__loading-dot" />
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
    </article>
  );
}

function RCAReportCard({
  summary,
  candidates,
  plan,
  timeline,
}: {
  summary: DiagnosisModifiedSummaryView;
  candidates: DiagnosisModifiedCandidateView[];
  plan?: DiagnosisModifiedPlanView;
  timeline: DiagnosisModifiedTimelineItem[];
}) {
  const primaryCandidate = candidates[0];
  const lastTimestamp = timeline[timeline.length - 1]?.timestamp;
  const lastParts = formatDateTimeParts(lastTimestamp);
  const evidenceRows = timeline
    .filter((item): item is Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> => item.kind === "tool")
    .slice(-3)
    .flatMap((item) => item.summaryLines.slice(0, 2).map((line) => ({ toolName: item.toolName, line })));

  return (
    <section className="diagnosis-modified-report-card">
      <header className="diagnosis-modified-report-card__header">
        <div>
          <div className="diagnosis-modified-report-card__eyebrow">
            <ToneBadge tone="neutral">RCA Report</ToneBadge>
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
          <span>Affected Services</span>
          <strong>{summary.affectedServices.length > 0 ? summary.affectedServices.join(", ") : "Pending"}</strong>
        </div>
        <div>
          <span>Trigger / Layer</span>
          <strong>{summary.subtitle}</strong>
        </div>
        <div>
          <span>Plan Priority</span>
          <strong>{plan?.priorityLabel ?? summary.priorityLabel ?? "--"}</strong>
        </div>
        <div>
          <span>AI Confidence</span>
          <strong>{plan?.confidenceLabel ?? summary.confidenceLabel}</strong>
        </div>
      </div>

      <div className="diagnosis-modified-report-card__sections">
        <div>
          <p className="diagnosis-modified-report-card__section-label">Symptom</p>
          <p className="diagnosis-modified-report-card__section-copy">{summary.impactSummary}</p>
        </div>
        <div>
          <p className="diagnosis-modified-report-card__section-label">Root Cause</p>
          <p className="diagnosis-modified-report-card__section-copy diagnosis-modified-report-card__section-copy--strong">
            {primaryCandidate ? `${primaryCandidate.title}。${primaryCandidate.summary}` : summary.title}
          </p>
        </div>
        <div>
          <p className="diagnosis-modified-report-card__section-label">Evidence & Logs</p>
          <div className="diagnosis-modified-report-card__evidence">
            {evidenceRows.length > 0 ? (
              evidenceRows.map((entry, index) => (
                <div key={`${entry.toolName}-${entry.line}-${index}`} className="diagnosis-modified-report-card__evidence-row">
                  <span>[{entry.toolName}]</span>
                  <span>{entry.line}</span>
                </div>
              ))
            ) : (
              <div className="diagnosis-modified-report-card__evidence-row">
                <span>[agent]</span>
                <span>Current timeline does not yet include enough tool observations to display here.</span>
              </div>
            )}
          </div>
        </div>
        <div>
          <p className="diagnosis-modified-report-card__section-label">Recommended Actions</p>
          <ul className="diagnosis-modified-report-card__actions">
            {(plan?.steps ?? []).slice(0, 3).map((step) => (
              <li key={step.id}>
                <span className={cn("diagnosis-modified-report-card__action-marker", step.status === "done" && "diagnosis-modified-report-card__action-marker--done")} />
                <div>
                  <span className={cn("diagnosis-modified-report-card__action-text", step.status === "done" && "diagnosis-modified-report-card__action-text--done")}>{step.title}</span>
                  <span className="diagnosis-modified-report-card__action-state">{step.status === "done" ? "Completed" : "Pending"}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

function ApprovalPlanCard({ plan }: { plan: DiagnosisModifiedPlanView }) {
  const [status, setStatus] = useState<ApprovalState>("pending");
  const [modifyInput, setModifyInput] = useState("");
  const [actions, setActions] = useState(plan.steps.map((step) => step.title));

  useEffect(() => {
    setStatus("pending");
    setModifyInput("");
    setActions(plan.steps.map((step) => step.title));
  }, [plan]);

  const handleSubmitModify = useCallback(() => {
    if (!modifyInput.trim()) {
      return;
    }

    setStatus("updating");
    window.setTimeout(() => {
      setActions((current) =>
        current.map((action, index) => {
          if (index === 1) {
            return "Only roll out to canary instances first, then observe Redis timeouts and error rate for 15 minutes before expanding scope.";
          }
          if (index === 2) {
            return "Before moving to full rollout, add one more verification pass for database wait queue and cache hit ratio.";
          }
          return action;
        }),
      );
      setModifyInput("");
      setStatus("pending");
    }, 1600);
  }, [modifyInput]);

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
            <ToneBadge tone="neutral">Execution Plan</ToneBadge>
            {status === "approved" ? (
              <ToneBadge tone="success">Approved</ToneBadge>
            ) : status === "rejected" ? (
              <ToneBadge tone="danger">Rejected</ToneBadge>
            ) : (
              <ToneBadge tone="accent">Requires Approval</ToneBadge>
            )}
          </div>
          <h3 className="diagnosis-modified-approval-card__title">{plan.title}</h3>
          <p className="diagnosis-modified-approval-card__description">{plan.description}</p>
        </div>
      </header>

      <div className="diagnosis-modified-approval-card__body">
        <div className="diagnosis-modified-approval-card__meta-row">
          <ToneBadge tone="warning">{plan.priorityLabel}</ToneBadge>
          <ToneBadge tone="neutral">淇″績 {plan.confidenceLabel}</ToneBadge>
          {plan.safetyLabel ? <ToneBadge tone="info">{plan.safetyLabel}</ToneBadge> : null}
          {plan.canaryLabel ? <ToneBadge tone="neutral">{plan.canaryLabel}</ToneBadge> : null}
        </div>

        <div className="diagnosis-modified-approval-card__actions-wrap">
          {status === "updating" ? <div className="diagnosis-modified-approval-card__updating">Updating plan...</div> : null}
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
              Approve & Execute
            </button>
            <button className="diagnosis-modified-action-btn" onClick={() => setStatus("rejected")} type="button">
              Reject
            </button>
            <button className="diagnosis-modified-action-btn" onClick={() => setStatus("modifying")} type="button">
              Modify Plan
            </button>
          </div>
        ) : null}

        {status === "modifying" ? (
          <div className="diagnosis-modified-approval-card__editor">
            <p>Provide feedback to agent</p>
            <div className="diagnosis-modified-approval-card__editor-row">
              <textarea
                onChange={(event) => setModifyInput(event.target.value)}
                placeholder="例如：不要一次性全量执行，先做 canary 验证。"
                value={modifyInput}
              />
              <div>
                <button
                  className="diagnosis-modified-action-btn diagnosis-modified-action-btn--primary"
                  disabled={!modifyInput.trim()}
                  onClick={handleSubmitModify}
                  type="button"
                >
                  Update Plan
                </button>
                <button className="diagnosis-modified-action-btn" onClick={() => setStatus("pending")} type="button">
                  Cancel
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {(status === "approved" || status === "rejected") ? (
          <div className="diagnosis-modified-approval-card__resolved">
            <div className="diagnosis-modified-avatar diagnosis-modified-avatar--assistant">
              <AppIcon name="spark" size={14} />
            </div>
            <p>{status === "approved" ? "Plan approved. The agent will now execute the actions." : "Plan rejected. The agent will await further instructions."}</p>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function DiagnosisModifiedPage() {
  const params = useParams<{ sessionId?: string }>();
  const [draft, setDraft] = useState("");
  const [demoTimeline, setDemoTimeline] = useState<DiagnosisModifiedTimelineItem[]>([]);
  const [demoCandidates, setDemoCandidates] = useState<DiagnosisModifiedCandidateView[]>([]);
  const [demoSummary, setDemoSummary] = useState<DiagnosisModifiedSummaryView | undefined>();
  const [demoPlan, setDemoPlan] = useState<DiagnosisModifiedPlanView | undefined>();
  const [demoState, setDemoState] = useState<"idle" | "running" | "complete">("idle");
  const [lastDemoPrompt, setLastDemoPrompt] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const demoTimerRef = useRef<number[]>([]);

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
    applyEvent,
    setConnectionState,
  } = useDiagnosisStore();

  const liveView = useMemo(() => buildDiagnosisModifiedLiveView(session, messages), [messages, session]);
  const hasLiveSession = bootstrapStatus === "ready" && Boolean(session) && Boolean(activeSessionId);

  useEffect(() => {
    void bootstrapSession(params.sessionId);
  }, [bootstrapSession, params.sessionId]);

  const clearDemoTimers = useCallback(() => {
    demoTimerRef.current.forEach((timerId) => window.clearTimeout(timerId));
    demoTimerRef.current = [];
  }, []);

  useEffect(() => clearDemoTimers, [clearDemoTimers]);

  const activeTimeline = hasLiveSession ? liveView.timeline : demoTimeline;
  const activeCandidates = hasLiveSession ? liveView.candidates : demoCandidates;
  const activeSummary = hasLiveSession ? liveView.summary : demoSummary;
  const activePlan = hasLiveSession ? liveView.plan : demoPlan;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activePlan, activeSummary, activeTimeline]);

  const websocketEnabled = import.meta.env.VITE_WS_ENABLED === "true" && Boolean(activeSessionId);
  const websocketUrl = useMemo(
    () =>
      buildBackendWsUrl(`/ws/thinking-trace/${activeSessionId ?? "pending"}`, {
        token: import.meta.env.VITE_API_TOKEN ?? "",
      }),
    [activeSessionId],
  );

  const handleRealtimeEvent = useCallback(
    (event: Parameters<typeof applyEvent>[0]) => {
      applyEvent(event);
    },
    [applyEvent],
  );

  const ws = useWebSocket(websocketUrl, handleRealtimeEvent, {
    enabled: websocketEnabled,
  });

  useEffect(() => {
    setConnectionState(ws.state);
  }, [setConnectionState, ws.state]);

  const resetDemo = useCallback(() => {
    clearDemoTimers();
    setDemoTimeline([]);
    setDemoCandidates([]);
    setDemoSummary(undefined);
    setDemoPlan(undefined);
    setDemoState("idle");
  }, [clearDemoTimers]);

  const startDemo = useCallback(
    (prompt: string) => {
      const normalizedPrompt = prompt.trim();
      if (!normalizedPrompt) {
        return;
      }

      clearDemoTimers();
      const scenario = buildDiagnosisModifiedDemoScenario(normalizedPrompt);
      setDemoTimeline(scenario.initialTimeline);
      setDemoCandidates([]);
      setDemoSummary(undefined);
      setDemoPlan(undefined);
      setDemoState("running");
      setLastDemoPrompt(normalizedPrompt);
      setDraft("");

      scenario.events.forEach((event: DiagnosisModifiedDemoEvent) => {
        const timerId = window.setTimeout(() => {
          if (event.type === "append") {
            setDemoTimeline((current) => [...current, event.item]);
            return;
          }

          if (event.type === "update_tool") {
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
            return;
          }

          if (event.type === "update_thinking") {
            setDemoTimeline((current) =>
              current.map((item) => {
                if (item.kind !== "thinking" || item.id !== event.targetId) {
                  return item;
                }

                return {
                  ...item,
                  status: event.status,
                };
              }),
            );
            return;
          }

          setDemoCandidates(scenario.candidates);
          setDemoSummary(scenario.summary);
          setDemoPlan(scenario.plan);
          setDemoState("complete");
        }, Math.round(event.delayMs * DEMO_EVENT_SLOWDOWN));

        demoTimerRef.current.push(timerId);
      });
    },
    [clearDemoTimers],
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

  const inlineError = useMemo(() => resolveInlineError(error), [error]);

  const composerDisabled = isLoadingSession || isSendingMessage || demoState === "running";
  const introCopy = hasLiveSession
    ? "当前页面正在消费真实诊断会话，并按 Toolcall 的线性工作流重新呈现思考、工具调用、RCA 与审批。"
    : demoState === "idle"
      ? "输入一个诊断问题后，将按更慢的 Toolcall 节奏回放完整诊断过程，方便你看清每一步思考和工具调用。"
      : "当前正在按慢速回放 Toolcall 风格诊断过程。";

  return (
    <div className="diagnosis-modified-page">
      <section className="diagnosis-modified-shell">
        <header className="diagnosis-modified-shell__header">
          <div className="diagnosis-modified-shell__title-group">
            <p className="diagnosis-modified-shell__eyebrow">Agent Root Cause Analyzer</p>
            <h1 className="diagnosis-modified-shell__title">Diagnosis (Modified)</h1>
            <p className="diagnosis-modified-shell__description">{introCopy}</p>
          </div>

          <div className="diagnosis-modified-shell__header-actions">
            <div className="diagnosis-modified-shell__badges">
              <ToneBadge tone={hasLiveSession ? "success" : "accent"}>{hasLiveSession ? "Live session" : "Demo mode"}</ToneBadge>
              {hasLiveSession && activeSessionId ? <ToneBadge tone="neutral">{activeSessionId}</ToneBadge> : null}
              {hasLiveSession ? <ToneBadge tone={connectionState === "open" ? "success" : "warning"}>{`Realtime link ${connectionState}`}</ToneBadge> : null}
            </div>
            {!hasLiveSession ? (
              <button className="diagnosis-modified-toolbar-btn" onClick={() => (demoTimeline.length > 0 ? startDemo(lastDemoPrompt || draft || "Analyze auth-svc latency and error-rate spike in the past hour") : resetDemo())} type="button">
                <AppIcon name="refresh" size={14} />
                <span>{demoTimeline.length > 0 ? "Replay demo" : "Clear"}</span>
              </button>
            ) : null}
          </div>
        </header>

        <div className="diagnosis-modified-shell__body">
          <div className="diagnosis-modified-feed">
            {activeTimeline.length === 0 ? (
              <div className="diagnosis-modified-empty-state">
                <div className="diagnosis-modified-empty-state__icon">
                  <AppIcon name="aiChat" size={18} />
                </div>
                <h2>Welcome to the RCA Agent</h2>
                <p>Type your request below to trigger the ReAct diagnostic process, or use the pre-filled example.</p>
              </div>
            ) : (
              activeTimeline.map((item, index) => {
                const shouldAnimate = !hasLiveSession && demoState === "running" && index >= Math.max(0, activeTimeline.length - 2);

                if (item.kind === "message") {
                  return <MessageRow animate={shouldAnimate} item={item} key={item.id} />;
                }

                if (item.kind === "thinking") {
                  return <ThinkingBlock animate={!hasLiveSession && item.status === "thinking"} item={item} key={item.id} />;
                }

                return <ToolCard item={item} key={item.id} />;
              })
            )}

            {hasLiveSession && traceStatus === "empty" ? (
              <div className="diagnosis-modified-inline-note">当前真实会话尚未产出 trace，页面会继续保留输入入口，等待后续诊断过程回流。</div>
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

            {activeSummary ? <RCAReportCard candidates={activeCandidates} plan={activePlan} summary={activeSummary} timeline={activeTimeline} /> : null}
            {activePlan ? <ApprovalPlanCard plan={activePlan} /> : null}
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
                  ? "继续追问当前诊断会话，例如：请解释为什么会出现这些候选根因。"
                  : "Ask the agent to diagnose an issue... (Press Enter to start)"
              }
              rows={1}
              value={draft}
            />
            <div className="diagnosis-modified-composer__actions">
              <p className="diagnosis-modified-composer__hint">
                {hasLiveSession ? "保留真实会话的数据流，界面按 Toolcall 的节奏与层级呈现。" : "无会话时会触发本地 demo，并按慢速回放 Toolcall 风格诊断流程。"}
              </p>
              <div className="diagnosis-modified-composer__buttons">
                {!hasLiveSession && demoTimeline.length > 0 ? (
                  <button className="diagnosis-modified-send-btn diagnosis-modified-send-btn--secondary" onClick={() => startDemo(lastDemoPrompt || draft || "Analyze auth-svc latency and error-rate spike in the past hour")} type="button">
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
          <p className="diagnosis-modified-shell__footer-note">Agent can make mistakes. Consider verifying important information.</p>
        </footer>
      </section>
    </div>
  );
}

export default DiagnosisModifiedPage;









