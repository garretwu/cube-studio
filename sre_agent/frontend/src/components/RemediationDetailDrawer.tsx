import { useEffect, useMemo, useState } from "react";

import type { DiagnosisSessionSummary, RemediationOverview, SessionEvent } from "../api/types";
import { formatDateTime, formatDurationSeconds, formatPercent } from "../utils/format";
import { formatRemediationPlanText, getRemediationStageLabel } from "../utils/remediationEventText";
import { deriveRemediationExecutionView } from "../utils/remediationProgress";
import { AppIcon } from "./ui";
import RemediationTimeline from "./RemediationTimeline";

type RemediationRecord = {
  summary: DiagnosisSessionSummary;
  overview?: RemediationOverview;
};

type RemediationDetailDrawerProps = {
  actionLoading: boolean;
  activeStepSelection?: { sessionId: string; stepId: number } | null;
  isLoading: boolean;
  onClose: () => void;
  onOpenApproval: () => void;
  onSelectStep?: (sessionId: string, stepId: number) => void;
  open: boolean;
  overview?: RemediationOverview;
  record: RemediationRecord | null;
  events: SessionEvent[];
};

const STATUS_LABELS: Record<string, string> = {
  approval_required: "等待审批",
  approval_rejected: "审批已拒绝",
  approved: "已批准",
  awaiting_approval: "等待审批",
  escalated: "已升级处理",
  execution_failed: "执行失败",
  execution_started: "开始修复",
  execution_succeeded: "执行成功",
  execution_timeout: "执行超时",
  failed: "失败",
  partially_resolved: "部分恢复",
  pending: "待开始",
  plan_revised: "方案已修订",
  proposed_fix_ready: "方案待执行",
  re_diagnosed: "复诊完成",
  rejected: "已拒绝",
  remediating: "修复中",
  resolved: "已恢复",
  rollback_failed: "回滚失败",
  rollback_started: "开始回滚",
  rollback_succeeded: "回滚成功",
  timeout: "超时",
  validating: "验证中",
};
const VERIFICATION_LABELS: Record<string, string> = {
  promql: "指标校验",
  tool_call: "工具校验",
  wait: "观察等待",
};

const TERMINAL_STATUSES = new Set(["resolved", "failed", "escalated", "timeout", "rejected"]);
const ATTENTION_STATUSES = new Set(["failed", "escalated", "timeout", "rejected", "execution_failed", "rollback_failed"]);
const ACTIVE_EXECUTION_STATUSES = new Set(["remediating", "execution_started", "validating"]);
const OBSERVATION_POLICY_LABELS: Record<string, string> = {
  default_alert_and_metrics: "告警恢复 + 指标改善",
  alert_status_only_when_post_metrics_unavailable: "仅告警恢复（观测指标缺失降级）",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sortEvents(events: SessionEvent[]): SessionEvent[] {
  return [...events].sort((left, right) => left.timestamp.localeCompare(right.timestamp));
}

function getEventStage(event: SessionEvent): string {
  if (event.type !== "remediation_progress") return event.type;
  const stage = String(event.data?.stage ?? "").trim();
  return stage || "remediation_progress";
}

function getStatusLabel(value?: string | null, fallback = "未知"): string {
  if (!value) return fallback;
  return STATUS_LABELS[value] ?? getRemediationStageLabel(value, fallback);
}

function getStatusTone(value?: string | null): "neutral" | "accent" | "success" | "warning" | "danger" | "info" {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (ATTENTION_STATUSES.has(normalized)) return "danger";
  if (TERMINAL_STATUSES.has(normalized) || normalized === "execution_succeeded" || normalized === "rollback_succeeded") return "success";
  if (normalized === "remediating" || normalized === "execution_started" || normalized === "validating") return "warning";
  return "neutral";
}

function getVerificationLabel(value?: string | null): string {
  if (!value) return "未知";
  return VERIFICATION_LABELS[value] ?? value;
}

function formatStepVerificationSummary(step: RemediationOverview["plan"]["steps"][number]): string {
  const verification = step.verification;
  const parts = [getVerificationLabel(verification.method)];
  if (verification.query) parts.push(verification.query);
  if (verification.tool) parts.push(verification.tool);
  if (verification.wait_seconds) parts.push(`${verification.wait_seconds}s`);
  return parts.join(" / ");
}

function getExecutionStartedAt(events: SessionEvent[] | undefined): string | null {
  if (!events?.length) return null;
  for (const event of sortEvents(events)) {
    if (getEventStage(event) === "execution_started") return event.timestamp;
  }
  return null;
}

function getApprover(events: SessionEvent[] | undefined): string | null {
  if (!events?.length) return null;
  const sortedEvents = sortEvents(events);
  const isLikelyHuman = (value: string) => !/(system|bot|auto[-_]?sre)/i.test(value);

  for (const event of sortedEvents) {
    const data = isRecord(event.data) ? event.data : {};
    if (event.type === "approval_required") {
      const user = typeof data.user === "string" ? data.user.trim() : "";
      if (user && isLikelyHuman(user)) return user;
    }
  }

  for (const event of sortedEvents) {
    const stage = getEventStage(event).toLowerCase();
    const data = isRecord(event.data) ? event.data : {};
    const user = typeof data.user === "string" ? data.user.trim() : "";
    if (!user || !isLikelyHuman(user)) continue;
    if (["approved", "rejected", "approval_rejected", "approval_required", "awaiting_approval"].includes(stage)) {
      return user;
    }
  }

  for (const event of sortedEvents) {
    const data = isRecord(event.data) ? event.data : {};
    if (typeof data.user === "string" && data.user.trim()) return data.user.trim();
  }
  return null;
}

function normalizeBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  return null;
}

function normalizePolicy(value: unknown): string {
  const normalized = typeof value === "string" ? value.trim() : "";
  return normalized || "default_alert_and_metrics";
}

function filterRemediationTimeline(events: SessionEvent[]): SessionEvent[] {
  if (!events.length) return [];
  const sorted = sortEvents(events);
  const approvalStartIndex = sorted.findIndex((event) => event.type === "approval_required");
  const remediationStartIndex = sorted.findIndex((event) => event.type === "remediation_progress");
  const startIndex = approvalStartIndex >= 0 ? approvalStartIndex : remediationStartIndex;
  if (startIndex < 0) return [];
  return sorted.slice(startIndex).filter((event) => {
    if (event.type === "approval_required" || event.type === "remediation_progress") return true;
    if (event.type === "plan_revised") return approvalStartIndex >= 0;
    return false;
  });
}

function getLatestObservationResult(events: SessionEvent[]): Record<string, unknown> | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "remediation_progress") continue;
    const stage = String(event.data?.stage ?? "").trim().toLowerCase();
    if (stage !== "observation_result") continue;
    return isRecord(event.data) ? event.data : null;
  }
  return null;
}

type TimelinePlaybackState = {
  activeIndex: number | null;
  completedCount: number;
  totalCount: number;
  visibleCount: number;
  overallPercent: number;
};

export default function RemediationDetailDrawer({
  actionLoading,
  events,
  isLoading,
  onClose,
  onOpenApproval,
  open,
  overview,
  record,
}: RemediationDetailDrawerProps) {
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const drawerOverview = record && (record.summary.session_id === overview?.session_id ? overview : record.overview);
  const hasLiveEventStream = Boolean(
    record?.summary.session_id &&
      overview?.session_id === record.summary.session_id &&
      events.length > 0,
  );
  const drawerEvents = useMemo(() => {
    const recordSessionId = record?.summary.session_id;
    const realtimeEvents =
      recordSessionId && overview?.session_id === recordSessionId
        ? events.filter((event) => event.session_id === recordSessionId)
        : [];
    return filterRemediationTimeline(realtimeEvents.length > 0 ? realtimeEvents : drawerOverview?.timeline ?? events);
  }, [drawerOverview?.timeline, events, overview?.session_id, record?.summary.session_id]);
  const summary = record?.summary;
  const drawerStatus = String(drawerOverview?.progress.status ?? record?.summary.status ?? "pending").trim();
  const drawerStartedAt = getExecutionStartedAt(drawerEvents);
  const drawerApprover = getApprover(drawerEvents);
  const drawerRootCause = drawerOverview?.plan.root_cause ?? record?.summary.root_cause ?? "待补充";
  const drawerImpact = drawerOverview?.plan.estimated_impact ?? "待补充";
  const drawerSummary = formatRemediationPlanText(drawerOverview?.plan.description) || record?.summary.summary || "";
  const drawerTitle = summary
    ? summary.title.replace(/\s*[·•]\s*(CRITICAL|WARNING|INFO)$/i, "").trim() || summary.title
    : "";
  const severityLabel = String(summary?.severity ?? "").trim().toUpperCase() || "UNKNOWN";
  const incidentLabel = String(summary?.session_id ?? "").trim();
  const executionSourceLabel = drawerOverview?.approval_required ? "待人工审批后执行" : "自动触发修复流程";
  const affectedServices = drawerOverview?.affected_services?.length
    ? drawerOverview.affected_services
    : (summary?.affected_services ?? []);
  const affectedServicesSummary = affectedServices.length ? affectedServices.join("、") : "未记录";
  const detailSteps = drawerOverview?.plan.steps ?? [];
  const observationResult = getLatestObservationResult(drawerEvents);
  const observationPolicy = normalizePolicy(observationResult?.policy_applied);
  const policyLabel = OBSERVATION_POLICY_LABELS[observationPolicy] ?? observationPolicy;
  const alertCleared = normalizeBoolean(observationResult?.alert_cleared);
  const metricsImproved = normalizeBoolean(observationResult?.metrics_improved);
  const alertOnlyPolicy = observationPolicy === "alert_status_only_when_post_metrics_unavailable";
  const observationPassed =
    alertCleared !== null && (alertOnlyPolicy ? alertCleared : alertCleared && metricsImproved === true);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [planStepsExpanded, setPlanStepsExpanded] = useState(false);
  const [strategyExpanded, setStrategyExpanded] = useState(true);
  const [timelinePlayback, setTimelinePlayback] = useState<TimelinePlaybackState>({
    activeIndex: null,
    completedCount: 0,
    totalCount: 0,
    visibleCount: 0,
    overallPercent: 0,
  });

  useEffect(() => {
    if (!open) return;
    setPlanStepsExpanded(false);
    setStrategyExpanded(true);
  }, [open, drawerOverview?.session_id]);

  useEffect(() => {
    setNowMs(Date.now());
  }, [drawerOverview?.session_id, open]);

  const executionView = useMemo(
    () =>
      deriveRemediationExecutionView({
        overview: drawerOverview ?? undefined,
        now: nowMs,
        fallbackStartedAt: summary?.started_at,
        fallbackUpdatedAt: summary?.updated_at,
        fallbackDurationSeconds: summary?.duration_seconds,
      }),
    [drawerOverview, nowMs, summary?.duration_seconds, summary?.started_at, summary?.updated_at],
  );
  const normalizedDrawerStatus = drawerStatus.trim().toLowerCase();
  const shouldAutoPlayExecution = detailSteps.length > 0 && ACTIVE_EXECUTION_STATUSES.has(normalizedDrawerStatus);
  const flowTotalSteps = detailSteps.length > 0 ? detailSteps.length : executionView.totalSteps;

  useEffect(() => {
    setTimelinePlayback({
      activeIndex: null,
      completedCount: 0,
      totalCount: 0,
      visibleCount: 0,
      overallPercent: 0,
    });
  }, [drawerOverview?.session_id, shouldAutoPlayExecution]);

  useEffect(() => {
    if (!open || !executionView.startedAt || executionView.isTerminal || typeof window === "undefined") {
      return undefined;
    }
    const timer = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [executionView.isTerminal, executionView.startedAt, open]);

  const displayTotalSteps = flowTotalSteps > 0 ? flowTotalSteps : executionView.totalSteps;
  const playbackBasedCompletedSteps = shouldAutoPlayExecution && timelinePlayback.totalCount > 0
    ? Math.floor((timelinePlayback.completedCount / Math.max(timelinePlayback.totalCount, 1)) * displayTotalSteps)
    : executionView.completedSteps;
  const displayCompletedSteps = Math.max(0, Math.min(displayTotalSteps, playbackBasedCompletedSteps));
  const displayOverallProgress = executionView.overallProgress;
  const displayCanaryProgress = drawerOverview?.plan.canary?.enabled
    ? shouldAutoPlayExecution && timelinePlayback.totalCount > 0
      ? executionView.canaryProgress
      : (executionView.canaryProgress ?? executionView.overallProgress)
    : null;

  if (!open || !record || !summary || !drawerOverview) return null;

  return (
    <div className="remediation-drawer remediation-drawer--mask" role="dialog" aria-modal="true" aria-label="修复详情">
      <button type="button" className="remediation-drawer__backdrop" aria-label="关闭修复详情" onClick={onClose} />
      <aside className="remediation-sidepanel remediation-sidepanel--overlay" role="complementary" aria-label="修复详情侧栏" onClick={(event) => event.stopPropagation()}>
        <div className="remediation-sidepanel__surface">
        <header className="remediation-sidepanel__header">
          <button type="button" className="remediation-sidepanel__back" onClick={onClose} aria-label="返回修复列表">
            <AppIcon name="arrowLeft" size={14} />
            <span>返回列表</span>
          </button>
          <div className="remediation-sidepanel__title-row">
            <h3 className="remediation-sidepanel__title">{drawerTitle}</h3>
            <span className="remediation-sidepanel-tag remediation-sidepanel-tag--severity">
              <span className="remediation-sidepanel-tag__dot" aria-hidden="true" />
              <span>{severityLabel}</span>
            </span>
            <span className={`remediation-sidepanel-tag remediation-sidepanel-tag--status remediation-sidepanel-tag--status-${getStatusTone(drawerStatus)}`}>
              <AppIcon name="spark" size={12} />
              <span>{getStatusLabel(drawerStatus)}</span>
            </span>
          </div>
          <p className="remediation-sidepanel__meta">
            <span>{incidentLabel.startsWith("INC-") ? incidentLabel : `INC-${incidentLabel}`}</span>
            <span aria-hidden="true">·</span>
            <span>{executionSourceLabel}</span>
          </p>
        </header>

        <div className="remediation-sidepanel__scroll">
          <section className="remediation-panel-section">
            <h4 className="remediation-panel-section__title">方案信息</h4>
            <div className="remediation-panel-kv-grid remediation-panel-kv-grid--plan">
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">受影响服务</span>
                <p className="remediation-panel-kv__value">{affectedServicesSummary}</p>
              </div>
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">方案优先级</span>
                <p className="remediation-panel-kv__value">{drawerOverview.plan.priority ?? "未记录"}</p>
              </div>
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">方案置信度</span>
                <p className="remediation-panel-kv__value">
                  {Number.isFinite(Number(drawerOverview.plan.confidence)) ? formatPercent(drawerOverview.plan.confidence) : "未记录"}
                </p>
              </div>
            </div>
            <div className="remediation-panel-summary">
              <p className="remediation-panel-summary__text">
                <strong>方案摘要:</strong>
                {" "}
                {drawerSummary || "未记录方案摘要"}
              </p>
              {detailSteps.length > 0 ? (
                <div className="remediation-panel-summary__steps">
                  <button
                    type="button"
                    className="remediation-panel-toggle"
                    onClick={() => setPlanStepsExpanded((current) => !current)}
                    aria-expanded={planStepsExpanded}
                    aria-label={planStepsExpanded ? "收起步骤详情" : "展开步骤详情"}
                  >
                    <AppIcon name={planStepsExpanded ? "up" : "down"} size={14} />
                    <span>{planStepsExpanded ? "收起步骤详情" : "展开步骤详情"}</span>
                  </button>
                  {planStepsExpanded ? (
                    <div className="remediation-panel-steps">
                      {detailSteps.map((step) => (
                        <div key={step.step_id} className="remediation-panel-step">
                          <p className="remediation-panel-step__title">{`步骤 ${step.step_id}`}</p>
                          <p className="remediation-panel-step__desc">{formatRemediationPlanText(step.description) || step.description}</p>
                          <p className="remediation-panel-step__meta">
                            {`工具：${step.tool} · 验证：${formatStepVerificationSummary(step)} · 超时：${step.timeout}s`}
                          </p>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </section>

          <section className="remediation-panel-section">
            <div className="remediation-panel-kv-grid remediation-panel-kv-grid--split">
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">根因定位</span>
                <p className="remediation-panel-kv__value">{drawerRootCause}</p>
              </div>
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">风险影响</span>
                <p className="remediation-panel-kv__value">{drawerImpact}</p>
              </div>
            </div>
          </section>

          <section className="remediation-panel-section remediation-panel-section--strategy">
            <div className={`remediation-panel-strategy${strategyExpanded ? " is-expanded" : ""}`}>
              <button
                type="button"
                className="remediation-panel-section__toggle remediation-panel-strategy__toggle"
                onClick={() => setStrategyExpanded((current) => !current)}
                aria-expanded={strategyExpanded}
                aria-label={strategyExpanded ? "收起成功判定策略" : "展开成功判定策略"}
              >
                <span className="remediation-panel-section__title remediation-panel-section__title--with-icon">
                  <AppIcon name={strategyExpanded ? "down" : "right"} size={14} />
                  <span>成功判定策略</span>
                </span>
              </button>
              {strategyExpanded ? (
                <div className="remediation-panel-strategy__body">
                  {drawerOverview.plan.canary?.enabled ? (
                    <>
                      <ul className="remediation-panel-strategy__list">
                        {drawerOverview.plan.canary.success_criteria.map((item, index) => (
                          <li key={`${item.metric}-${index}`} className="remediation-panel-strategy__item">
                            {`${item.metric} ${item.operator} ${String(item.value)}`}
                          </li>
                        ))}
                      </ul>
                      <p className="remediation-panel-strategy__meta">
                        {`灰度目标流量 ${formatPercent(drawerOverview.plan.canary.target_percentage)}，观察窗口 ${drawerOverview.plan.canary.monitor_duration}s。`}
                      </p>
                    </>
                  ) : (
                    observationResult ? (
                      <>
                        <ul className="remediation-panel-strategy__list">
                          <li className="remediation-panel-strategy__item">{`策略口径：${policyLabel}`}</li>
                          <li className="remediation-panel-strategy__item">{`alert_cleared：${alertCleared === null ? "未知" : (alertCleared ? "是" : "否")}`}</li>
                          <li className="remediation-panel-strategy__item">
                            {`metrics_improved：${
                              metricsImproved === null
                                ? (alertOnlyPolicy ? "未纳入（策略降级）" : "未知")
                                : (metricsImproved ? "是" : "否")
                            }`}
                          </li>
                          <li className="remediation-panel-strategy__item">{`观察结论：${observationPassed ? "通过" : "未通过"}`}</li>
                        </ul>
                        <p className="remediation-panel-strategy__meta">当前方案未启用灰度，按观察结果判定是否成功。</p>
                      </>
                    ) : (
                      <p className="remediation-panel-strategy__meta">当前方案未启用灰度，等待观察结果。</p>
                    )
                  )}
                </div>
              ) : null}
            </div>
          </section>

          {(drawerOverview.plan_history?.length ?? 0) > 0 ? (
            <section className="remediation-panel-section remediation-panel-section--revisions">
              <div className="remediation-revision-list">
                {drawerOverview.plan_history?.map((item) => (
                  <div className="remediation-revision-item" key={`${item.plan_id}-${item.version}`}>
                    <span className="remediation-revision-item__dot" aria-hidden="true" />
                    <div className="remediation-revision-item__body">
                      <p className="remediation-revision-item__title">{`方案修订 v${item.version}`}</p>
                      <p className="remediation-revision-item__copy">{item.instruction || "未记录修订指令"}</p>
                    </div>
                    <time className="remediation-revision-item__time">{item.revised_at ? formatDateTime(item.revised_at) : "未知"}</time>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          <section className="remediation-panel-section">
            <h4 className="remediation-panel-section__title">执行过程</h4>
            <div className="remediation-panel-kv-grid remediation-panel-kv-grid--execution">
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">开始时间</span>
                <p className="remediation-panel-kv__value">{executionView.startedAt ? formatDateTime(executionView.startedAt) : (drawerStartedAt ? formatDateTime(drawerStartedAt) : "尚未开始")}</p>
              </div>
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">持续时长</span>
                <p className="remediation-panel-kv__value">{formatDurationSeconds(executionView.durationSeconds)}</p>
              </div>
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">审批人</span>
                <p className="remediation-panel-kv__value">{drawerApprover ?? "未记录"}</p>
              </div>
              <div className="remediation-panel-kv">
                <span className="remediation-panel-kv__label">最近更新</span>
                <p className="remediation-panel-kv__value">{formatDateTime(executionView.updatedAt ?? summary.updated_at)}</p>
              </div>
            </div>
            <div className="remediation-panel-progress">
              <div className="remediation-panel-progress__head">
                <span className="remediation-panel-progress__title">当前推进</span>
                <span className="remediation-panel-progress__percent">{`总体 ${displayOverallProgress}%`}</span>
              </div>
              <div className="progress-track remediation-progress-track remediation-progress-track--thin">
                <div
                  className="progress-track__fill remediation-progress-track__fill remediation-progress-track__fill--neutral"
                  style={{ width: `${displayCanaryProgress ?? displayOverallProgress}%` }}
                />
              </div>
              <p className="remediation-panel-progress__meta">
                {drawerOverview.plan.canary?.enabled
                  ? `灰度 ${displayCanaryProgress ?? 0}%`
                  : "未启用灰度，按全量策略执行。"}
              </p>
            </div>
          </section>

          <section className="remediation-panel-section remediation-panel-section--timeline">
            <h4 className="remediation-panel-section__title">修复事件</h4>
            <RemediationTimeline
              events={drawerEvents}
              sessionId={drawerOverview.session_id}
              autoPlay={shouldAutoPlayExecution && !hasLiveEventStream}
              onPlaybackChange={setTimelinePlayback}
            />
          </section>
        </div>
        </div>
      </aside>
    </div>
  );
}
