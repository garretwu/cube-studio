import { useEffect, useMemo, type CSSProperties } from "react";

import type { DiagnosisSessionSummary, RemediationOverview, SessionEvent } from "../api/types";
import { formatDateTime, formatDurationSeconds, formatPercent } from "../utils/format";
import { AppIcon, StatusChip } from "./ui";
import RemediationTimeline from "./RemediationTimeline";

type RemediationRecord = {
  summary: DiagnosisSessionSummary;
  overview?: RemediationOverview;
};

type RemediationDetailDrawerProps = {
  actionLoading: boolean;
  activeStepSelection: { sessionId: string; stepId: number } | null;
  isLoading: boolean;
  onClose: () => void;
  onOpenApproval: () => void;
  onSelectStep: (sessionId: string, stepId: number) => void;
  open: boolean;
  overview?: RemediationOverview;
  record: RemediationRecord | null;
  events: SessionEvent[];
};

const STATUS_LABELS: Record<string, string> = {
  approval_accepted: "审批已接受",
  approval_required: "等待审批",
  approval_rejected: "审批已拒绝",
  approved: "已批准",
  awaiting_approval: "等待审批",
  escalated: "已升级处理",
  execution_failed: "执行失败",
  execution_mocked: "模拟执行完成",
  execution_started: "开始修复",
  execution_succeeded: "执行成功",
  execution_timeout: "执行超时",
  failed: "失败",
  observation_result: "观察结果已采集",
  observation_started: "开始观察",
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
  return STATUS_LABELS[value] ?? value;
}

function getVerificationLabel(value?: string | null): string {
  if (!value) return "未知";
  return VERIFICATION_LABELS[value] ?? value;
}

function getStatusTone(value?: string | null): "neutral" | "accent" | "success" | "warning" | "danger" | "info" {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (ATTENTION_STATUSES.has(normalized)) return "danger";
  if (TERMINAL_STATUSES.has(normalized) || normalized === "execution_succeeded" || normalized === "rollback_succeeded") return "success";
  if (normalized === "remediating" || normalized === "execution_started" || normalized === "validating") return "warning";
  if (normalized === "approval_required" || normalized === "awaiting_approval") return "info";
  if (normalized === "approved" || normalized === "plan_revised") return "accent";
  return "neutral";
}

function formatResult(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatStepVerificationSummary(step: RemediationOverview["plan"]["steps"][number]): string {
  const verification = step.verification;
  const parts = [getVerificationLabel(verification.method)];
  if (verification.query) parts.push(verification.query);
  if (verification.tool) parts.push(verification.tool);
  if (verification.wait_seconds) parts.push(`${verification.wait_seconds}s`);
  return parts.join(" / ");
}

function getOverallProgress(overview?: RemediationOverview): number {
  if (!overview) return 0;
  const total = Number(overview.progress.total_steps ?? 0);
  const completed = Number(overview.progress.completed_steps ?? 0);
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
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
  for (const event of sortEvents(events)) {
    const data = isRecord(event.data) ? event.data : {};
    if (typeof data.user === "string" && data.user.trim()) return data.user.trim();
  }
  return null;
}

function getStepState(
  stepIndex: number,
  completedSteps: number,
  totalSteps: number,
  status?: string | null,
): "completed" | "current" | "pending" {
  if (totalSteps <= 0) return "pending";

  const normalizedStatus = String(status ?? "").trim().toLowerCase();
  if (completedSteps >= totalSteps || normalizedStatus === "resolved") {
    return "completed";
  }

  if (stepIndex < Math.max(0, completedSteps)) {
    return "completed";
  }

  if (stepIndex === Math.min(Math.max(0, completedSteps), totalSteps - 1)) {
    return normalizedStatus === "pending" || normalizedStatus === "approval_required" || normalizedStatus === "awaiting_approval"
      ? "pending"
      : "current";
  }

  return "pending";
}

function getCanaryProgress(overview?: RemediationOverview): number | null {
  if (!overview?.plan.canary?.enabled) return null;
  const batches = overview.progress.batch_status ?? [];
  if (batches.length > 0) {
    const canaryBatch = batches.find((item) => /canary/i.test(item.batch)) ?? batches[0];
    return Math.max(0, Math.min(100, Math.round(Number(canaryBatch.progress ?? 0))));
  }
  if (String(overview.progress.status ?? "").trim().toLowerCase() === "resolved") return 100;
  return getOverallProgress(overview);
}
function getCurrentStepSummary(
  steps: RemediationOverview["plan"]["steps"],
  completedSteps: number,
  status?: string | null,
): string {
  if (steps.length === 0) return "暂无执行步骤";

  const normalizedStatus = String(status ?? "").trim().toLowerCase();
  if (completedSteps >= steps.length || normalizedStatus === "resolved") {
    return `已完成全部 ${steps.length} 个步骤`;
  }

  if (normalizedStatus === "pending" || normalizedStatus === "approval_required" || normalizedStatus === "awaiting_approval") {
    const firstStep = steps[0];
    return `待开始 · 步骤 ${firstStep.step_id} ${firstStep.description}`;
  }

  const currentStep = steps[Math.min(Math.max(completedSteps, 0), steps.length - 1)];
  return `步骤 ${currentStep.step_id} · ${currentStep.description}`;
}

function formatEvidenceValue(value: unknown): string {
  if (value === null || value === undefined) return "未采集";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return formatResult(value);
}


export default function RemediationDetailDrawer({
  actionLoading,
  activeStepSelection,
  events,
  isLoading,
  onClose,
  onOpenApproval,
  onSelectStep,
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
  const drawerEvents = useMemo(() => {
    const allEvents = sortEvents(drawerOverview?.timeline ?? events);
    const approvalIndex = allEvents.findIndex((event) => event.type === "approval_required");
    if (approvalIndex === -1) return allEvents;
    return allEvents.slice(approvalIndex);
  }, [drawerOverview?.timeline, events]);
  const drawerStatus = String(drawerOverview?.progress.status ?? record?.summary.status ?? "pending").trim();
  const drawerStartedAt = getExecutionStartedAt(drawerEvents);
  const drawerApprover = getApprover(drawerEvents);
  const drawerRootCause = drawerOverview?.plan.root_cause ?? record?.summary.root_cause ?? "待补充";
  const drawerImpact = drawerOverview?.plan.estimated_impact ?? record?.summary.root_cause ?? "待补充";
  const drawerSummary = drawerOverview?.plan.description ?? record?.summary.summary ?? "";
  const affectedServicesSummary = record?.summary.affected_services?.length ? record.summary.affected_services.join("、") : "未记录";
  const detailSteps = drawerOverview?.plan.steps ?? [];
  const completedSteps = Number(drawerOverview?.progress.completed_steps ?? 0);
  const totalSteps = Number(drawerOverview?.progress.total_steps ?? detailSteps.length);
  const overallProgress = getOverallProgress(drawerOverview ?? undefined);
  const canaryProgress = getCanaryProgress(drawerOverview ?? undefined);
  const canaryStrategy = drawerOverview?.plan.canary?.enabled
    ? `${formatPercent(drawerOverview.plan.canary.target_percentage)} / ${drawerOverview.plan.canary.monitor_duration}s 观察`
    : "未启用灰度";
  const canaryProgressSummary = drawerOverview?.plan.canary?.enabled
    ? `${canaryProgress ?? 0}% · ${canaryStrategy}`
    : "未启用灰度";
  const baselineReview = drawerOverview?.baseline_review ?? null;
  const preAlert = baselineReview?.pre_check?.alert ?? null;
  const postAlert = baselineReview?.post_check?.alert ?? null;
  const alertReview = baselineReview?.alert_review ?? null;
  const metricReviews = baselineReview?.metric_reviews ?? [];
  const currentStepSummary = getCurrentStepSummary(detailSteps, completedSteps, drawerStatus);
  const activeStep =
    drawerOverview && detailSteps.length > 0 && activeStepSelection?.sessionId === drawerOverview.session_id
      ? detailSteps.find((step) => step.step_id === activeStepSelection.stepId) ?? null
      : null;
  const stepFlowProgress =
    drawerOverview && detailSteps.length > 0
      ? Math.max(
          0,
          Math.min(100, Math.round((Number(drawerOverview.progress.completed_steps ?? 0) / Math.max(Number(drawerOverview.progress.total_steps ?? detailSteps.length), 1)) * 100)),
        )
      : 0;

  if (!open || !record || !drawerOverview) {
    return null;
  }

  return (
    <div className="remediation-drawer" role="dialog" aria-modal="true" aria-label="修复详情">
      <button type="button" className="remediation-drawer__backdrop" aria-label="关闭修复详情" onClick={onClose} />
      <aside className="remediation-drawer__panel" onClick={(event) => event.stopPropagation()}>
        <div className="remediation-drawer__header">
          <div className="remediation-drawer__heading">
            <button type="button" className="remediation-drawer__back-button" onClick={onClose} aria-label="返回修复列表">
              <AppIcon name="arrowLeft" size={16} />
              <span>返回</span>
            </button>
            <span className="remediation-drawer__kicker">记录明细</span>
            <h4 className="remediation-drawer__title">{record.summary.title}</h4>
            <p className="remediation-drawer__note">查看方案、执行步骤与修复时间线。</p>
            <div className="status-row remediation-drawer__chips">
              <StatusChip tone={getStatusTone(drawerStatus)}>{getStatusLabel(drawerStatus)}</StatusChip>
              {drawerOverview.plan.priority ? <StatusChip tone="accent">{drawerOverview.plan.priority}</StatusChip> : null}
              {drawerOverview.plan.confidence ? <StatusChip tone="info">{`置信度 ${formatPercent(drawerOverview.plan.confidence)}`}</StatusChip> : null}
              {drawerOverview.plan_version ? <StatusChip tone="neutral">{`当前方案 v${drawerOverview.plan_version}`}</StatusChip> : null}
            </div>
          </div>
        </div>

        <div className="remediation-drawer__body">
          <div className="remediation-record-table__subsection">
            <div className="remediation-record-table__subsection-header">
              <div>
                <p className="remediation-record-table__subsection-title">方案信息</p>
                <p className="remediation-record-table__subsection-copy">先看关键概览，再看根因、影响和方案修订记录。</p>
              </div>
            </div>

            <div className="remediation-plan-overview">
              <div className="remediation-plan-overview__facts">
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="calendar" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">开始时间</span>
                    <p className="remediation-plan-overview__fact-value">{drawerStartedAt ? formatDateTime(drawerStartedAt) : "尚未开始"}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="timeCircle" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">持续时长</span>
                    <p className="remediation-plan-overview__fact-value">{formatDurationSeconds(record.summary.duration_seconds)}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="users3" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">审批人</span>
                    <p className="remediation-plan-overview__fact-value">{drawerApprover ?? "未记录"}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="serverRack" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">受影响服务</span>
                    <p className="remediation-plan-overview__fact-value">{affectedServicesSummary}</p>
                  </div>
                </div>
              </div>

              <div className="remediation-plan-overview__facts remediation-plan-overview__facts--detail">
                <div className="remediation-plan-overview__fact remediation-plan-overview__fact--wide">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="document" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">方案摘要</span>
                    <p className="remediation-plan-overview__fact-value">{drawerSummary || "未记录方案摘要"}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="helpSquare" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">根因定位</span>
                    <p className="remediation-plan-overview__fact-value">{drawerRootCause}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="chart" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">风险影响</span>
                    <p className="remediation-plan-overview__fact-value">{drawerImpact}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="timeCircle" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">最近更新</span>
                    <p className="remediation-plan-overview__fact-value">{formatDateTime(record.summary.updated_at)}</p>
                  </div>
                </div>
              </div>
            </div>

            <div className="remediation-plan-overview__history">
              {(drawerOverview.plan_history?.length ?? 0) === 0 ? (
                <p className="data-list__copy">当前只有基础方案版本，尚未出现修订记录。</p>
              ) : (
                drawerOverview.plan_history?.map((item) => (
                  <div className="remediation-plan-overview__fact remediation-plan-overview__fact--wide" key={`${item.plan_id}-${item.version}`}>
                    <span className="remediation-plan-overview__fact-icon">
                      <AppIcon name="edit" size={18} />
                    </span>
                    <div className="remediation-plan-overview__fact-body">
                      <span className="remediation-plan-overview__fact-label">{`方案修订 v${item.version}`}</span>
                      <p className="remediation-plan-overview__fact-value">{item.instruction || "未记录修订指令"}</p>
                      <p className="remediation-plan-overview__fact-meta">{`修订时间：${item.revised_at ? formatDateTime(item.revised_at) : "未知"}`}</p>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="remediation-record-table__subsection">
            <div className="remediation-record-table__subsection-header">
              <div>
                <p className="remediation-record-table__subsection-title">执行信息</p>
                <p className="remediation-record-table__subsection-copy">先看当前状态和步骤，再看推进进度与成功判定。</p>
              </div>
            </div>
            <div className="remediation-plan-overview remediation-plan-overview--compact">
              <div className="remediation-plan-overview__facts remediation-plan-overview__facts--execution">
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="infoCircle" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">最新状态</span>
                    <p className="remediation-plan-overview__fact-value">{getStatusLabel(drawerStatus)}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="layers" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">当前步骤</span>
                    <p className="remediation-plan-overview__fact-value">{currentStepSummary}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="rocket" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">灰度进度</span>
                    <p className="remediation-plan-overview__fact-value">{canaryProgressSummary}</p>
                  </div>
                </div>
                <div className="remediation-plan-overview__fact">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="chart" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body">
                    <span className="remediation-plan-overview__fact-label">全量进度</span>
                    <p className="remediation-plan-overview__fact-value">{`${overallProgress}% · ${completedSteps} / ${totalSteps}`}</p>
                  </div>
                </div>
              </div>
              <div className="remediation-plan-overview__history remediation-plan-overview__history--execution">
                <div className="remediation-plan-overview__fact remediation-plan-overview__fact--wide remediation-plan-overview__fact--stacked">
                  <span className="remediation-plan-overview__fact-icon">
                    <AppIcon name="chart" size={18} />
                  </span>
                  <div className="remediation-plan-overview__fact-body remediation-plan-overview__fact-body--wide">
                    <span className="remediation-plan-overview__fact-label">当前推进</span>
                    <p className="remediation-plan-overview__fact-value">
                      {drawerOverview.plan.canary?.enabled
                        ? `灰度进度 ${canaryProgress ?? 0}% ，全量进度 ${overallProgress}%`
                        : `当前未启用灰度，正在按全量步骤推进，整体进度 ${overallProgress}%`}
                    </p>
                    <p className="remediation-plan-overview__fact-meta">
                      {drawerOverview.plan.canary?.enabled
                        ? `当前观察 ${formatPercent(drawerOverview.plan.canary.target_percentage)} 流量，观察窗口 ${drawerOverview.plan.canary.monitor_duration}s；已完成 ${completedSteps} / ${totalSteps} 个执行步骤。`
                        : `当前已完成 ${completedSteps} / ${totalSteps} 个执行步骤，系统将按既定步骤继续执行。`}
                    </p>
                    <div className="progress-track remediation-progress-track remediation-progress-track--canary">
                      <div className="progress-track__fill remediation-progress-track__fill remediation-progress-track__fill--canary" style={{ width: `${canaryProgress ?? overallProgress}%` }} />
                    </div>
                  </div>
                </div>
                {drawerOverview.plan.canary?.enabled ? (
                  <div className="remediation-plan-overview__fact remediation-plan-overview__fact--wide remediation-plan-overview__fact--stacked">
                    <span className="remediation-plan-overview__fact-icon">
                      <AppIcon name="documentCheck" size={18} />
                    </span>
                    <div className="remediation-plan-overview__fact-body remediation-plan-overview__fact-body--wide">
                      <span className="remediation-plan-overview__fact-label">成功判定</span>
                      <div className="remediation-execution-criteria">
                        {drawerOverview.plan.canary.success_criteria.map((item, index) => (
                          <p className="remediation-execution-criteria__item" key={`${item.metric}-${index}`}>
                            {`${item.metric} ${item.operator} ${String(item.value)}`}
                          </p>
                        ))}
                      </div>
                      <p className="remediation-plan-overview__fact-meta">满足以上条件后，灰度阶段即可视为通过。</p>
                    </div>
                  </div>
                ) : (
                  <div className="remediation-plan-overview__fact remediation-plan-overview__fact--wide remediation-plan-overview__fact--stacked">
                    <span className="remediation-plan-overview__fact-icon">
                      <AppIcon name="documentCheck" size={18} />
                    </span>
                    <div className="remediation-plan-overview__fact-body remediation-plan-overview__fact-body--wide">
                      <span className="remediation-plan-overview__fact-label">成功判定</span>
                      <p className="remediation-plan-overview__fact-value">当前方案未配置灰度校验条件，执行完成后将按整体结果统一确认。</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="remediation-record-table__subsection">
            <div className="remediation-record-table__subsection-header">
              <div>
                <p className="remediation-record-table__subsection-title">执行步骤</p>
                <p className="remediation-record-table__subsection-copy">逐步查看动作、验证方式和回滚能力。</p>
              </div>
            </div>
            <div className="remediation-step-flow">
              {detailSteps.length > 0 ? (
                <>
                  <div className="remediation-step-flow__track" style={{ "--remediation-step-flow-done-width": `${stepFlowProgress}%` } as CSSProperties}>
                    <span className="remediation-step-flow__rail remediation-step-flow__rail--base" />
                    <span className="remediation-step-flow__rail remediation-step-flow__rail--done" />
                    {detailSteps.map((step, index) => {
                      const completedSteps = Number(drawerOverview.progress.completed_steps ?? 0);
                      const totalSteps = Number(drawerOverview.progress.total_steps ?? detailSteps.length);
                      const stepState = getStepState(index, completedSteps, totalSteps, drawerStatus);
                      const isActive = activeStep?.step_id === step.step_id;
                      const iconName = stepState === "completed" ? "checkmarkCircle" : stepState === "current" ? "timeCircle" : "infoCircle";

                      return (
                        <div key={step.step_id} className="remediation-step-flow__segment">
                          <button
                            type="button"
                            className={`remediation-step-flow__step remediation-step-flow__step--${stepState}${isActive ? " remediation-step-flow__step--active" : ""}`}
                            onClick={() => onSelectStep(drawerOverview.session_id, step.step_id)}
                            aria-expanded={isActive}
                            aria-label={`查看步骤 ${step.step_id} 详情`}
                          >
                            <span className={`remediation-step-flow__node remediation-step-flow__node--${stepState}`}>
                              <AppIcon name={iconName} size={24} />
                            </span>
                            <span className={`remediation-step-flow__badge remediation-step-flow__badge--${stepState}`}>{`步骤 ${step.step_id}`}</span>
                            <span className="remediation-step-flow__title">{step.description}</span>
                            <span className="remediation-step-flow__meta">{`工具 / ${step.tool}`}</span>
                            <span className="remediation-step-flow__meta remediation-step-flow__meta--secondary">{formatStepVerificationSummary(step)}</span>
                            <span className="remediation-step-flow__action">
                              <AppIcon name={isActive ? "up" : "down"} size={14} />
                              <span>{isActive ? "收起详情" : "查看详情"}</span>
                            </span>
                          </button>
                        </div>
                      );
                    })}
                  </div>
                  {activeStep ? (
                    <div className="remediation-step-flow__detail">
                      <div className="remediation-step-flow__detail-header">
                        <div className="remediation-step-flow__detail-heading">
                          <p className="remediation-step-flow__detail-title">{`步骤 ${activeStep.step_id} 详情`}</p>
                          <p className="remediation-step-flow__detail-copy">{activeStep.description}</p>
                        </div>
                        <div className="status-row remediation-step-flow__detail-chips">
                          <StatusChip tone={getStatusTone(drawerStatus)}>{getStatusLabel(drawerStatus)}</StatusChip>
                          <StatusChip tone="neutral">{activeStep.tool}</StatusChip>
                          <StatusChip tone="info">{getVerificationLabel(activeStep.verification.method)}</StatusChip>
                          <StatusChip tone="neutral">{`超时 ${activeStep.timeout}s`}</StatusChip>
                        </div>
                      </div>
                      <div className="remediation-record-table__detail-form remediation-step-flow__detail-form">
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">步骤编号</p>
                          <p className="remediation-record-table__detail-value">{activeStep.step_id}</p>
                        </div>
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">执行描述</p>
                          <p className="remediation-record-table__detail-value">{activeStep.description}</p>
                        </div>
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">执行工具</p>
                          <p className="remediation-record-table__detail-value">{activeStep.tool}</p>
                        </div>
                        <div className="remediation-record-table__detail-field remediation-record-table__detail-field--wide">
                          <p className="remediation-record-table__detail-label">执行参数</p>
                          <p className="remediation-record-table__detail-value remediation-code-block">{formatResult(activeStep.params)}</p>
                        </div>
                        {activeStep.command ? (
                          <div className="remediation-record-table__detail-field remediation-record-table__detail-field--wide">
                            <p className="remediation-record-table__detail-label">执行命令</p>
                            <p className="remediation-record-table__detail-value remediation-code-block">
                              <code>{activeStep.command}</code>
                            </p>
                          </div>
                        ) : null}
                        <div className="remediation-record-table__detail-field remediation-record-table__detail-field--wide">
                          <p className="remediation-record-table__detail-label">验证方式</p>
                          <p className="remediation-record-table__detail-value">{formatStepVerificationSummary(activeStep)}</p>
                        </div>
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">回滚策略</p>
                          <p className="remediation-record-table__detail-value">{activeStep.rollback_tool || "未定义回滚动作"}</p>
                        </div>
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">超时时间</p>
                          <p className="remediation-record-table__detail-value">{`${activeStep.timeout}s`}</p>
                        </div>
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">验证方法</p>
                          <p className="remediation-record-table__detail-value">{getVerificationLabel(activeStep.verification.method)}</p>
                        </div>
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">验证查询</p>
                          <p className="remediation-record-table__detail-value">{activeStep.verification.query ?? "无"}</p>
                        </div>
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">验证工具</p>
                          <p className="remediation-record-table__detail-value">{activeStep.verification.tool ?? "无"}</p>
                        </div>
                        <div className="remediation-record-table__detail-field">
                          <p className="remediation-record-table__detail-label">验证等待</p>
                          <p className="remediation-record-table__detail-value">{typeof activeStep.verification.wait_seconds === "number" ? `${activeStep.verification.wait_seconds}s` : "无"}</p>
                        </div>
                      </div>
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="mini-card remediation-empty-state remediation-empty-state--compact remediation-record-table__embedded-card">
                  <p className="mini-card__title">暂无执行步骤</p>
                  <p className="mini-card__copy">当前方案尚未包含可展开显示的步骤明细。</p>
                </div>
              )}
            </div>
          </div>

          <div className="remediation-record-table__subsection">
            <div className="remediation-record-table__subsection-header">
              <div>
                <p className="remediation-record-table__subsection-title">基线与复查</p>
                <p className="remediation-record-table__subsection-copy">对比修复前后的告警状态与 LLM 服务指标，确认是否真正恢复。</p>
              </div>
            </div>
            {baselineReview ? (
              <div className="remediation-plan-overview remediation-plan-overview--compact">
                <div className="remediation-plan-overview__facts remediation-plan-overview__facts--detail">
                  <div className="remediation-plan-overview__fact">
                    <span className="remediation-plan-overview__fact-icon">
                      <AppIcon name="notification" size={18} />
                    </span>
                    <div className="remediation-plan-overview__fact-body">
                      <span className="remediation-plan-overview__fact-label">修复前告警</span>
                      <p className="remediation-plan-overview__fact-value">{preAlert ? `${preAlert.alert_name} · ${preAlert.status}` : "未采集"}</p>
                      <p className="remediation-plan-overview__fact-meta">{preAlert ? `firing=${String(preAlert.is_firing)}` : "未记录修复前告警状态"}</p>
                    </div>
                  </div>
                  <div className="remediation-plan-overview__fact">
                    <span className="remediation-plan-overview__fact-icon">
                      <AppIcon name="notification" size={18} />
                    </span>
                    <div className="remediation-plan-overview__fact-body">
                      <span className="remediation-plan-overview__fact-label">修复后告警</span>
                      <p className="remediation-plan-overview__fact-value">{postAlert ? `${postAlert.alert_name} · ${postAlert.status}` : "未采集"}</p>
                      <p className="remediation-plan-overview__fact-meta">
                        {alertReview ? (alertReview.cleared ? "告警已清除" : "告警未清除，需人工介入") : "未记录复查结论"}
                      </p>
                    </div>
                  </div>
                  <div className="remediation-plan-overview__fact">
                    <span className="remediation-plan-overview__fact-icon">
                      <AppIcon name="chart" size={18} />
                    </span>
                    <div className="remediation-plan-overview__fact-body">
                      <span className="remediation-plan-overview__fact-label">指标复查</span>
                      <p className="remediation-plan-overview__fact-value">
                        {baselineReview.metrics_improved === true ? "指标已改善" : baselineReview.metrics_improved === false ? "指标未达预期" : "未完成判断"}
                      </p>
                      <p className="remediation-plan-overview__fact-meta">{`共复查 ${metricReviews.length} 项指标`}</p>
                    </div>
                  </div>
                  <div className="remediation-plan-overview__fact">
                    <span className="remediation-plan-overview__fact-icon">
                      <AppIcon name="documentCheck" size={18} />
                    </span>
                    <div className="remediation-plan-overview__fact-body">
                      <span className="remediation-plan-overview__fact-label">最终结论</span>
                      <p className="remediation-plan-overview__fact-value">
                        {baselineReview.alert_cleared === true && baselineReview.metrics_improved === true ? "告警清除且指标恢复" : "仍需继续观察或人工介入"}
                      </p>
                    </div>
                  </div>
                </div>
                <div className="remediation-plan-overview__history">
                  {metricReviews.length > 0 ? (
                    metricReviews.map((item) => (
                      <div className="remediation-plan-overview__fact remediation-plan-overview__fact--wide" key={`${item.metric_key}-${item.query}`}>
                        <span className="remediation-plan-overview__fact-icon">
                          <AppIcon name="chart" size={18} />
                        </span>
                        <div className="remediation-plan-overview__fact-body">
                          <span className="remediation-plan-overview__fact-label">{item.metric_key}</span>
                          <p className="remediation-plan-overview__fact-value">{`${formatEvidenceValue(item.before_value)} -> ${formatEvidenceValue(item.after_value)}`}</p>
                          <p className="remediation-plan-overview__fact-meta">
                            {item.available ? (item.improved ? "已改善" : "未改善") : item.error || "指标暂不可用"}
                          </p>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="data-list__copy">当前没有可展示的前后指标对比。</p>
                  )}
                </div>
              </div>
            ) : (
              <div className="mini-card remediation-empty-state remediation-empty-state--compact remediation-record-table__embedded-card">
                <p className="mini-card__title">尚未采集基线与复查数据</p>
                <p className="mini-card__copy">当前会话还没有完整的修复前后证据，执行修复后会在这里展示对比结果。</p>
              </div>
            )}
          </div>

          <div className="remediation-record-table__subsection">
            <div className="remediation-record-table__subsection-header">
              <div>
                <p className="remediation-record-table__subsection-title">修复时间线</p>
                <p className="remediation-record-table__subsection-copy">按时间查看审批、执行、观察和结论。</p>
              </div>
            </div>
            <RemediationTimeline events={drawerEvents} sessionId={drawerOverview.session_id} />
          </div>
        </div>
      </aside>
    </div>
  );
}









