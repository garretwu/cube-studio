import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import { apiClient } from "../api/client";
import type { DiagnosisSessionSummary, RemediationOverview, SessionEvent } from "../api/types";
import ApprovalDialog from "../components/ApprovalDialog";
import CanaryProgress from "../components/CanaryProgress";
import { AppButton, AppIcon, AppInput, MetricTile, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useRemediationStore } from "../store/remediationStore";
import { formatDateTime, formatDurationSeconds, formatPercent, formatTimestamp } from "../utils/format";

type StepResult = {
  step_id?: number;
  tool?: string;
  command?: string;
  result?: unknown;
  success?: boolean;
  mocked?: boolean;
  message?: string;
};

type RemediationRecord = {
  summary: DiagnosisSessionSummary;
  overview?: RemediationOverview;
};

type StatusFilter = "all" | "approval" | "running" | "done" | "attention";

const STATUS_LABELS: Record<string, string> = {
  approval_required: "待审批",
  approval_rejected: "已驳回",
  approved: "已批准",
  awaiting_approval: "待审批",
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
  rejected: "已驳回",
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
const RELEVANT_OUTCOMES = new Set(["proposed_fix_ready", "resolved", "partially_resolved", "failed", "escalated", "rejected", "timeout"]);
const RELEVANT_STATUSES = new Set([
  "approval_required",
  "awaiting_approval",
  "approved",
  "remediating",
  "resolved",
  "failed",
  "escalated",
  "timeout",
  "rejected",
  "diagnosed",
  "re_diagnosed",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sortEvents(events: SessionEvent[]): SessionEvent[] {
  return [...events].sort((left, right) => left.timestamp.localeCompare(right.timestamp));
}

function getEventStage(event: SessionEvent): string {
  if (event.type !== "remediation_progress") return event.type;
  const stage = String(event.data?.["stage"] ?? "").trim();
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

function getEventMessage(event: SessionEvent): string {
  const data = isRecord(event.data) ? event.data : {};
  if (typeof data.message === "string" && data.message.trim()) return data.message;
  return getStatusLabel(getEventStage(event));
}

function getStepResults(event: SessionEvent): StepResult[] {
  const data = isRecord(event.data) ? event.data : {};
  const raw = data.step_results;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is StepResult => isRecord(item));
}

function formatResult(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isRemediationRelevant(summary: DiagnosisSessionSummary): boolean {
  return RELEVANT_OUTCOMES.has(String(summary.outcome ?? "").trim()) || RELEVANT_STATUSES.has(String(summary.status ?? "").trim());
}

function getApprover(events: SessionEvent[] | undefined): string | null {
  if (!events?.length) return null;
  for (const event of sortEvents(events)) {
    const data = isRecord(event.data) ? event.data : {};
    if (typeof data.user === "string" && data.user.trim()) return data.user.trim();
  }
  return null;
}

function getExecutionStartedAt(events: SessionEvent[] | undefined): string | null {
  if (!events?.length) return null;
  for (const event of sortEvents(events)) {
    if (getEventStage(event) === "execution_started") return event.timestamp;
  }
  return null;
}

function getOverallProgress(overview?: RemediationOverview): number {
  if (!overview) return 0;
  const total = Number(overview.progress.total_steps ?? 0);
  const completed = Number(overview.progress.completed_steps ?? 0);
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
}

function getCanaryProgress(overview?: RemediationOverview): number | null {
  if (!overview?.plan.canary?.enabled) return null;
  const batches = overview.progress.batch_status ?? [];
  if (batches.length > 0) {
    const canaryBatch = batches.find((item) => /canary|金丝雀/i.test(item.batch)) ?? batches[0];
    return Math.max(0, Math.min(100, Math.round(Number(canaryBatch.progress ?? 0))));
  }
  if (String(overview.progress.status ?? "").trim().toLowerCase() === "resolved") return 100;
  return getOverallProgress(overview);
}

function getCanarySummary(overview?: RemediationOverview): string {
  if (!overview?.plan.canary?.enabled) return "未启用";
  if ((overview.progress.batch_status?.length ?? 0) > 0) return `${getCanaryProgress(overview) ?? 0}%`;
  return `目标 ${formatPercent(overview.plan.canary.target_percentage)} · 观察 ${overview.plan.canary.monitor_duration}s`;
}

function matchesFilter(record: RemediationRecord, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  const status = String(record.overview?.progress.status ?? record.summary.status ?? "").trim().toLowerCase();
  if (filter === "approval") return status === "approval_required" || status === "awaiting_approval";
  if (filter === "running") return status === "remediating";
  if (filter === "done") return status === "resolved";
  return ATTENTION_STATUSES.has(status);
}

function buildSearchText(record: RemediationRecord): string {
  return [
    record.summary.session_id,
    record.summary.title,
    record.summary.alert_name,
    record.summary.root_cause,
    record.summary.summary,
    record.overview?.plan.root_cause,
    record.overview?.plan.description,
    getApprover(record.overview?.timeline),
  ].filter(Boolean).join(" ").toLowerCase();
}
function RemediationPage() {
  const { overview, events, approvalDialogOpen, fetchOverview, isLoading, setApprovalDialogOpen, setSessionId, submitApproval } =
    useRemediationStore();
  const [records, setRecords] = useState<RemediationRecord[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsError, setRecordsError] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [actionLoading, setActionLoading] = useState(false);

  const loadRecords = useCallback(async (preferredSessionId?: string) => {
    setRecordsLoading(true);
    setRecordsError(null);
    try {
      const summaries = (await apiClient.getDiagnosisHistorySessions())
        .filter(isRemediationRelevant)
        .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
      const detailResults = await Promise.allSettled(
        summaries.map(async (summary) => ({
          summary,
          overview: await apiClient.getRemediationOverview(summary.session_id),
        })),
      );
      const nextRecords = summaries.map((summary, index) => {
        const settled = detailResults[index];
        return settled?.status === "fulfilled" ? settled.value : { summary };
      });
      setRecords(nextRecords);
      setSelectedSessionId((current) => {
        const candidate = preferredSessionId || current;
        if (candidate && nextRecords.some((item) => item.summary.session_id === candidate)) return candidate;
        return "";
      });
    } catch (error) {
      setRecords([]);
      setRecordsError(error instanceof Error ? error.message : "修复记录加载失败");
    } finally {
      setRecordsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRecords();
  }, [loadRecords]);

  useEffect(() => {
    if (!selectedSessionId) return;
    setSessionId(selectedSessionId);
    void fetchOverview(selectedSessionId);
  }, [fetchOverview, selectedSessionId, setSessionId]);

  useEffect(() => {
    if (!overview?.session_id) return;
    setRecords((current) => current.map((record) => (record.summary.session_id === overview.session_id ? { ...record, overview } : record)));
  }, [overview]);

  const filteredRecords = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return records.filter((record) => matchesFilter(record, statusFilter) && (!normalizedQuery || buildSearchText(record).includes(normalizedQuery)));
  }, [query, records, statusFilter]);

  const selectedRecord = useMemo(
    () => records.find((record) => record.summary.session_id === selectedSessionId) ?? null,
    [records, selectedSessionId],
  );

  const detailOverview = selectedRecord?.summary.session_id === overview?.session_id ? overview : selectedRecord?.overview;
  const pendingApprovalCount = records.filter((record) => {
    const status = String(record.overview?.progress.status ?? record.summary.status ?? "").trim().toLowerCase();
    return status === "approval_required" || status === "awaiting_approval";
  }).length;
  const runningCount = records.filter((record) => String(record.overview?.progress.status ?? record.summary.status ?? "").trim().toLowerCase() === "remediating").length;
  const canaryEnabledCount = records.filter((record) => Boolean(record.overview?.plan.canary?.enabled)).length;

  const handleRefresh = useCallback(async () => {
    if (!selectedSessionId) {
      await loadRecords();
      return;
    }
    setActionLoading(true);
    try {
      await fetchOverview(selectedSessionId);
      await loadRecords(selectedSessionId);
    } finally {
      setActionLoading(false);
    }
  }, [fetchOverview, loadRecords, selectedSessionId]);

  const handleSubmitApproval = useCallback(
    async (approved: boolean) => {
      setActionLoading(true);
      try {
        await submitApproval(approved);
        await loadRecords(selectedSessionId);
      } finally {
        setActionLoading(false);
      }
    },
    [loadRecords, selectedSessionId, submitApproval],
  );

  return (
    <div className="page-grid remediation-page">
      <div className="page-intro">
        <SectionHeader
          title="修复与执行"
          description="按修复记录查看审批、版本、金丝雀策略和执行结果。"
          actions={
            <AppButton iconLeft="refresh" loading={actionLoading} onClick={() => void handleRefresh()} variant="secondary">
              刷新数据
            </AppButton>
          }
        />
        <div className="card-grid--metrics remediation-page__metrics">
          <MetricTile hint="已识别到修复流程的诊断会话" label="修复记录" value={records.length} />
          <MetricTile hint="等待人工批准后进入执行" label="待审批" value={pendingApprovalCount} />
          <MetricTile hint="正在执行或观察验证中" label="执行中" value={runningCount} />
          <MetricTile hint="已配置灰度或金丝雀策略" label="金丝雀方案" value={canaryEnabledCount} />
        </div>
      </div>

      <div className="remediation-layout">
        <aside className="remediation-sidebar">
          <SurfaceCard title="修复记录" description="按状态和关键词快速定位会话，点击行后在表内展开子表单式详情。">
            <div className="page-stack remediation-sidebar__body">
              <div className="remediation-toolbar">
                <AppInput
                  value={query}
                  onChange={setQuery}
                  placeholder="搜索诊断 ID、名称、根因或审批人"
                  prefix={<AppIcon name="search" size={16} />}
                />
                <div className="remediation-filter-row">
                  {([
                    ["all", "全部"],
                    ["approval", "待审批"],
                    ["running", "执行中"],
                    ["done", "已恢复"],
                    ["attention", "需关注"],
                  ] as const).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      className={`remediation-filter-chip${statusFilter === value ? " remediation-filter-chip--active" : ""}`}
                      onClick={() => setStatusFilter(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {recordsLoading ? <p className="data-list__copy">正在同步修复记录...</p> : null}
              {recordsError ? <p className="data-list__copy remediation-sidebar__error">{recordsError}</p> : null}
              {!recordsLoading && filteredRecords.length === 0 ? (
                <div className="mini-card remediation-empty-state">
                  <p className="mini-card__title">暂无匹配的修复记录</p>
                  <p className="mini-card__copy">可以尝试放宽筛选条件，或等待新的诊断会话生成修复计划。</p>
                </div>
              ) : null}

              <div className="remediation-record-table-shell">
                <table className="remediation-record-table">
                  <thead>
                    <tr>
                      <th>修复记录</th>
                      <th>状态</th>
                      <th>修复内容</th>
                      <th>审批人</th>
                      <th>开始修复</th>
                      <th>金丝雀</th>
                      <th>全量</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRecords.map((record) => {
                      const recordOverview = record.overview;
                      const currentStatus = String(recordOverview?.progress.status ?? record.summary.status ?? "pending").trim();
                      const approver = getApprover(recordOverview?.timeline) ?? (recordOverview?.approval_required ? "待审批" : "未记录");
                      const startedAt = getExecutionStartedAt(recordOverview?.timeline);
                      const totalProgress = getOverallProgress(recordOverview);
                      const canaryProgress = getCanaryProgress(recordOverview);
                      const isExpanded = selectedRecord?.summary.session_id === record.summary.session_id;

                      return (
                        <Fragment key={record.summary.session_id}>
                          <tr
                            className={`remediation-record-table__row${isExpanded ? " remediation-record-table__row--active" : ""}`}
                            onClick={() =>
                              setSelectedSessionId((current) =>
                                current === record.summary.session_id ? "" : record.summary.session_id,
                              )
                            }
                          >
                            <td className="remediation-record-table__cell remediation-record-table__cell--record">
                              <span className="remediation-record-table__chevron">
                                <AppIcon name={isExpanded ? "down" : "right"} size={14} />
                              </span>
                              <div className="remediation-record-table__record">
                                <strong>{record.summary.title}</strong>
                                <span>{record.summary.session_id}</span>
                              </div>
                            </td>
                            <td className="remediation-record-table__cell remediation-record-table__cell--status">
                              <StatusChip tone={getStatusTone(currentStatus)}>{getStatusLabel(currentStatus)}</StatusChip>
                            </td>
                            <td className="remediation-record-table__cell remediation-record-table__cell--description">
                              <div className="remediation-record-table__description" title={recordOverview?.plan.description ?? record.summary.root_cause ?? record.summary.summary}>
                                {recordOverview?.plan.description ?? record.summary.root_cause ?? record.summary.summary}
                              </div>
                            </td>
                            <td className="remediation-record-table__cell">{approver}</td>
                            <td className="remediation-record-table__cell">{startedAt ? formatTimestamp(startedAt) : "未开始"}</td>
                            <td className="remediation-record-table__cell remediation-record-table__cell--progress">
                              <div className="remediation-record-table__progress-cell">
                                <span>{recordOverview?.plan.canary?.enabled ? getCanarySummary(recordOverview) : "未启用"}</span>
                                <div className="progress-track remediation-progress-track remediation-progress-track--canary">
                                  <div className="progress-track__fill remediation-progress-track__fill remediation-progress-track__fill--canary" style={{ width: `${canaryProgress ?? 0}%` }} />
                                </div>
                              </div>
                            </td>
                            <td className="remediation-record-table__cell remediation-record-table__cell--progress">
                              <div className="remediation-record-table__progress-cell">
                                <span>{totalProgress}%</span>
                                <div className="progress-track remediation-progress-track">
                                  <div className="progress-track__fill remediation-progress-track__fill" style={{ width: `${totalProgress}%` }} />
                                </div>
                              </div>
                            </td>
                          </tr>
                          {isExpanded ? (() => {
                            const expandedOverview = record.summary.session_id === overview?.session_id ? overview : recordOverview;
                            const expandedEvents = expandedOverview?.session_id === overview?.session_id ? sortEvents(events) : sortEvents(expandedOverview?.timeline ?? []);
                            const expandedStatus = String(expandedOverview?.progress.status ?? record.summary.status ?? "pending").trim();
                            const expandedApprover = getApprover(expandedEvents) ?? (expandedOverview?.approval_required ? "待审批" : "未记录");
                            const expandedStartedAt = getExecutionStartedAt(expandedEvents);
                            return (
                              <tr className="remediation-record-table__expand-row">
                                <td className="remediation-record-table__expand-cell" colSpan={7}>
                                  <div className="remediation-record-table__expand-panel">
                                    <div className="remediation-record-table__expand-header">
                                      <div className="remediation-record-table__expand-heading">
                                        <span className="remediation-record-table__expand-kicker">记录明细</span>
                                        <h4 className="remediation-record-table__expand-title">{record.summary.title}</h4>
                                        <p className="remediation-record-table__expand-note">{expandedOverview?.plan.description ?? record.summary.summary}</p>
                                      </div>
                                      <div className="remediation-record-table__expand-actions">
                                        <div className="status-row remediation-record-table__expand-chips">
                                          <StatusChip tone={getStatusTone(expandedStatus)}>{getStatusLabel(expandedStatus)}</StatusChip>
                                          {expandedOverview?.plan.priority ? <StatusChip tone="accent">{expandedOverview.plan.priority}</StatusChip> : null}
                                          {expandedOverview?.plan.confidence ? <StatusChip tone="info">{`置信度 ${formatPercent(expandedOverview.plan.confidence)}`}</StatusChip> : null}
                                          {expandedOverview?.plan_version ? <StatusChip tone="neutral">{`当前方案 v${expandedOverview.plan_version}`}</StatusChip> : null}
                                        </div>
                                        <div className="remediation-record-table__expand-action-buttons">
                                          <AppButton onClick={() => setSelectedSessionId("")} variant="tertiary">
                                            收起详细记录
                                          </AppButton>
                                          <AppButton disabled={!expandedOverview?.approval_required || actionLoading || isLoading} onClick={() => setApprovalDialogOpen(true)} variant="primary">
                                            进入审批
                                          </AppButton>
                                        </div>
                                      </div>
                                    </div>

                                    <div className="remediation-record-table__subsection">
                                      <div className="remediation-record-table__subsection-header"><div><p className="remediation-record-table__subsection-title">基础信息</p><p className="remediation-record-table__subsection-copy">汇总诊断、审批人与当前记录状态。</p></div></div>
                                      <div className="remediation-record-table__detail-form">
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">诊断 ID</span><strong className="remediation-record-table__detail-value">{record.summary.session_id}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">诊断名称</span><strong className="remediation-record-table__detail-value">{record.summary.alert_name}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">审批人</span><strong className="remediation-record-table__detail-value">{expandedApprover}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">审批状态</span><strong className="remediation-record-table__detail-value">{expandedOverview?.approval_required ? "等待批准" : getStatusLabel(expandedStatus)}</strong></div>
                                      <div className="remediation-record-table__detail-field remediation-record-table__detail-field--wide"><span className="remediation-record-table__detail-label">根因定位</span><strong className="remediation-record-table__detail-value">{expandedOverview?.plan.root_cause ?? record.summary.root_cause ?? "待补充"}</strong></div>
                                      <div className="remediation-record-table__detail-field remediation-record-table__detail-field--wide"><span className="remediation-record-table__detail-label">修复内容</span><p className="remediation-record-table__detail-value remediation-record-table__detail-value--multiline">{expandedOverview?.plan.description ?? record.summary.summary}</p></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">开始修复</span><strong className="remediation-record-table__detail-value">{expandedStartedAt ? formatDateTime(expandedStartedAt) : "尚未开始"}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">处理耗时</span><strong className="remediation-record-table__detail-value">{formatDurationSeconds(record.summary.duration_seconds)}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">方案版本</span><strong className="remediation-record-table__detail-value">{expandedOverview?.plan_version ? `v${expandedOverview.plan_version}` : "v1"}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">优先级</span><strong className="remediation-record-table__detail-value">{expandedOverview?.plan.priority ?? "P?"}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">更新时间</span><strong className="remediation-record-table__detail-value">{formatDateTime(record.summary.updated_at)}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">最新状态</span><strong className="remediation-record-table__detail-value">{getStatusLabel(expandedStatus)}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">风险影响</span><strong className="remediation-record-table__detail-value">{expandedOverview?.plan.estimated_impact ?? "待补充"}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">金丝雀策略</span><strong className="remediation-record-table__detail-value">{expandedOverview?.plan.canary?.enabled ? `目标 ${formatPercent(expandedOverview.plan.canary.target_percentage)} · 观察 ${expandedOverview.plan.canary.monitor_duration}s` : "未启用"}</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">全量进度</span><strong className="remediation-record-table__detail-value">{getOverallProgress(expandedOverview)}%</strong></div>
                                      <div className="remediation-record-table__detail-field"><span className="remediation-record-table__detail-label">已完成步骤</span><strong className="remediation-record-table__detail-value">{expandedOverview?.progress.completed_steps ?? 0} / {expandedOverview?.progress.total_steps ?? 0}</strong></div>
                                      </div>
                                    </div>

                                    <div className="remediation-record-table__subsection">
                                      <div className="remediation-record-table__subsection-header"><div><p className="remediation-record-table__subsection-title">方案信息</p><p className="remediation-record-table__subsection-copy">查看方案修订记录与当前审批状态。</p></div></div>
                                      <div className="remediation-version-list remediation-record-table__embedded-list">
                                        {(expandedOverview?.plan_history?.length ?? 0) === 0 ? (
                                          <p className="data-list__copy">当前只有基础方案版本，尚未出现修订记录。</p>
                                        ) : (
                                          expandedOverview?.plan_history?.map((item) => (
                                            <div className="remediation-version-item" key={`${item.plan_id}-${item.version}`}>
                                              <div className="status-row"><StatusChip tone="accent">{`v${item.version}`}</StatusChip><StatusChip tone="neutral">{item.plan_id}</StatusChip></div>
                                              <p className="data-list__title">{item.instruction || "未记录修订指令"}</p>
                                              <p className="data-list__copy">{`修订时间：${item.revised_at ? formatDateTime(item.revised_at) : "未知"}`}</p>
                                            </div>
                                          ))
                                        )}
                                      </div>
                                    </div>

                                    <div className="remediation-record-table__subsection">
                                      <div className="remediation-record-table__subsection-header"><div><p className="remediation-record-table__subsection-title">执行信息</p><p className="remediation-record-table__subsection-copy">把灰度策略和全量推进情况收拢到同一块明细里。</p></div></div>
                                      <div className="page-stack remediation-record-table__embedded-list">
                                        <div className="remediation-inline-grid remediation-inline-grid--compact">
                                          <div className="remediation-stat-card remediation-stat-card--canary"><span>金丝雀策略</span><strong>{expandedOverview?.plan.canary?.enabled ? `${formatPercent(expandedOverview.plan.canary.target_percentage)} / ${expandedOverview.plan.canary.monitor_duration}s` : "未启用"}</strong></div>
                                          <div className="remediation-stat-card"><span>全量进度</span><strong>{getOverallProgress(expandedOverview)}%</strong></div>
                                          <div className="remediation-stat-card"><span>已完成步骤</span><strong>{expandedOverview?.progress.completed_steps ?? 0} / {expandedOverview?.progress.total_steps ?? 0}</strong></div>
                                          <div className="remediation-stat-card"><span>最新状态</span><strong>{getStatusLabel(expandedStatus)}</strong></div>
                                        </div>
                                        <CanaryProgress batches={expandedOverview?.progress.batch_status ?? []} />
                                        {expandedOverview?.plan.canary?.enabled ? (
                                          <div className="remediation-criteria-list">
                                            {expandedOverview.plan.canary.success_criteria.map((item, index) => (
                                              <div className="remediation-criteria-item" key={`${item.metric}-${index}`}><span>{item.metric}</span><strong>{item.operator} {String(item.value)}</strong></div>
                                            ))}
                                          </div>
                                        ) : (
                                          <p className="data-list__copy">当前方案未启用金丝雀验证，将直接按整体步骤执行。</p>
                                        )}
                                      </div>
                                    </div>
                                    <div className="remediation-record-table__subsection">
                                      <div className="remediation-record-table__subsection-header"><div><p className="remediation-record-table__subsection-title">执行步骤</p><p className="remediation-record-table__subsection-copy">逐步查看动作、验证方式和回滚能力。</p></div></div>
                                      <div className="mini-card-list remediation-step-list remediation-record-table__embedded-list">
                                        {(expandedOverview?.plan.steps ?? []).map((step) => (
                                          <div key={step.step_id} className="mini-card remediation-step-card remediation-record-table__embedded-card">
                                            <div className="status-row">
                                              <StatusChip tone="accent">{`步骤 ${step.step_id}`}</StatusChip>
                                              <StatusChip tone="neutral">{step.tool}</StatusChip>
                                              <StatusChip tone="info">{getVerificationLabel(step.verification.method)}</StatusChip>
                                              <StatusChip tone="neutral">{`超时 ${step.timeout}s`}</StatusChip>
                                            </div>
                                            <p className="mini-card__title">{step.description}</p>
                                            <div className="entity-grid remediation-step-card__grid">
                                              <div className="entity-grid__row"><p className="entity-grid__label">执行参数</p><p className="entity-grid__value remediation-code-block">{formatResult(step.params)}</p></div>
                                              <div className="entity-grid__row"><p className="entity-grid__label">验证方式</p><p className="entity-grid__value">{getVerificationLabel(step.verification.method)}{step.verification.query ? ` · ${step.verification.query}` : ""}{step.verification.tool ? ` · ${step.verification.tool}` : ""}{step.verification.wait_seconds ? ` · ${step.verification.wait_seconds}s` : ""}</p></div>
                                              <div className="entity-grid__row"><p className="entity-grid__label">回滚策略</p><p className="entity-grid__value">{step.rollback_tool ? step.rollback_tool : "未定义回滚动作"}</p></div>
                                            </div>
                                          </div>
                                        ))}
                                      </div>
                                    </div>

                                    <div className="remediation-record-table__subsection">
                                      <div className="remediation-record-table__subsection-header"><div><p className="remediation-record-table__subsection-title">修复时间线</p><p className="remediation-record-table__subsection-copy">按时间查看审批、执行、观察和结论。</p></div></div>
                                      <div className="mini-card-list remediation-timeline-list remediation-record-table__embedded-list">
                                        {expandedEvents.length === 0 ? (
                                          <div className="mini-card remediation-empty-state remediation-empty-state--compact remediation-record-table__embedded-card">
                                            <p className="mini-card__title">暂无执行事件</p>
                                            <p className="mini-card__copy">当前记录尚未进入执行阶段，或后端还没有返回可用事件流。</p>
                                          </div>
                                        ) : (
                                          expandedEvents.map((event, index) => {
                                            const stage = getEventStage(event);
                                            const data = isRecord(event.data) ? event.data : {};
                                            const stepResults = getStepResults(event);
                                            const timeoutSeconds = data.timeout_seconds;
                                            return (
                                              <div key={`${event.type}-${event.timestamp}-${index}`} className="mini-card remediation-event-card remediation-record-table__embedded-card">
                                                <div className="remediation-event-card__header">
                                                  <div className="status-row"><StatusChip tone={getStatusTone(stage)}>{getStatusLabel(stage)}</StatusChip><StatusChip tone="neutral">{formatTimestamp(event.timestamp)}</StatusChip></div>
                                                  {typeof data.user === "string" && data.user.trim() ? <p className="mini-card__copy">{`操作人：${data.user}`}</p> : null}
                                                </div>
                                                <p className="mini-card__title">{getEventMessage(event)}</p>
                                                {typeof timeoutSeconds === "number" ? <p className="mini-card__copy">{`超时阈值：${timeoutSeconds} 秒`}</p> : null}
                                                {stepResults.length > 0 ? (
                                                  <div className="remediation-step-result-list">
                                                    {stepResults.map((item, itemIndex) => (
                                                      <div className="remediation-step-result" key={`${stage}-${item.step_id ?? itemIndex}`}>
                                                        <p className="mini-card__copy">{`步骤 ${item.step_id ?? itemIndex + 1} · 工具 ${item.tool ?? "-"} · 结果`}{item.success === false ? "失败" : "成功"}{item.mocked ? "（mock）" : ""}</p>
                                                        <p className="mini-card__copy">{`命令：${item.command ?? "-"}`}</p>
                                                        <p className="mini-card__copy">{`输出：${formatResult(item.result ?? item.message ?? "-")}`}</p>
                                                      </div>
                                                    ))}
                                                  </div>
                                                ) : null}
                                              </div>
                                            );
                                          })
                                        )}
                                      </div>
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            );
                          })() : null}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </SurfaceCard>
        </aside>
      </div>

      <ApprovalDialog
        onApprove={() => void handleSubmitApproval(true)}
        onCancel={() => setApprovalDialogOpen(false)}
        onReject={() => void handleSubmitApproval(false)}
        open={approvalDialogOpen}
        plan={detailOverview?.plan}
      />
    </div>
  );
}

export default RemediationPage;
