import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiClient } from "../api/client";
import type { DiagnosisSessionSummary, RemediationOverview, SessionEvent, WSEvent } from "../api/types";
import { buildBackendWsUrl } from "../api/ws";
import { getAccessTokenSync, getAuthRecoveryState, hasAccessToken, recoverAuthSession } from "../auth/tokenManager";
import ApprovalDialog from "../components/ApprovalDialog";
import RemediationDetailDrawer from "../components/RemediationDetailDrawer";
import { AppIcon, AppInput, MetricTile, StatusChip, SurfaceCard } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useRemediationStore } from "../store/remediationStore";
import { formatPercent, formatTimestamp } from "../utils/format";
import { getRemediationOverallProgressDisplay } from "../utils/remediationProgress";

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

const TERMINAL_STATUSES = new Set(["resolved", "failed", "escalated", "timeout", "rejected"]);
const ATTENTION_STATUSES = new Set(["failed", "escalated", "timeout", "rejected", "execution_failed", "rollback_failed"]);
const LIVE_REFRESH_STATUSES = new Set(["approval_required", "awaiting_approval", "remediating", "validating"]);
const REALTIME_FALLBACK_POLL_MS = 10000;
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
  const stage = String(event.data?.stage ?? "").trim();
  return stage || "remediation_progress";
}

function getStatusLabel(value?: string | null, fallback = "未知"): string {
  if (!value) return fallback;
  return STATUS_LABELS[value] ?? value;
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
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function RemediationPage() {
  const {
    overview,
    events,
    lastEventId,
    realtimeState,
    approvalDialogOpen,
    fetchOverview,
    reconcileEvents,
    applyRealtimeEvent,
    isLoading,
    setApprovalDialogOpen,
    setSessionId,
    setRealtimeState,
    submitApproval,
  } = useRemediationStore();
  const [records, setRecords] = useState<RemediationRecord[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsError, setRecordsError] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [activeStepSelection, setActiveStepSelection] = useState<{ sessionId: string; stepId: number } | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [actionLoading, setActionLoading] = useState(false);
  const initialRequestedSessionIdRef = useRef(new URLSearchParams(window.location.search).get("sessionId")?.trim() ?? "");

  const loadRecords = useCallback(async (preferredSessionId?: string) => {
    setRecordsLoading(true);
    setRecordsError(null);
    try {
      const historySessions: DiagnosisSessionSummary[] = await apiClient.getDiagnosisHistorySessions();
      const summaries = historySessions
        .filter(isRemediationRelevant)
        .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
      const detailResults = await Promise.allSettled(
        summaries.map(async (summary): Promise<RemediationRecord> => ({
          summary,
          overview: await apiClient.getRemediationOverview(summary.session_id),
        })),
      );
      const nextRecords: RemediationRecord[] = summaries.map((summary, index) => {
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
    const preferredSessionId = initialRequestedSessionIdRef.current || undefined;
    initialRequestedSessionIdRef.current = "";
    void loadRecords(preferredSessionId);
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

  useEffect(() => {
    setActiveStepSelection(null);
  }, [selectedSessionId]);

  const filteredRecords = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return records.filter((record) => matchesFilter(record, statusFilter) && (!normalizedQuery || buildSearchText(record).includes(normalizedQuery)));
  }, [query, records, statusFilter]);

  const selectedRecord = useMemo(
    () => records.find((record) => record.summary.session_id === selectedSessionId) ?? null,
    [records, selectedSessionId],
  );
  const activeOverview =
    selectedRecord && overview?.session_id === selectedRecord.summary.session_id
      ? overview
      : selectedRecord?.overview;
  const activeStatus = String(
    activeOverview?.progress.status ?? selectedRecord?.summary.status ?? "",
  )
    .trim()
    .toLowerCase();
  const websocketEnabled =
    import.meta.env.VITE_WS_ENABLED === "true" &&
    hasAccessToken() &&
    getAuthRecoveryState() !== "terminal" &&
    selectedSessionId.length > 0;
  const shouldUseFallbackPolling =
    selectedSessionId.length > 0 &&
    LIVE_REFRESH_STATUSES.has(activeStatus) &&
    realtimeState !== "open";
  const realtimeChip = useMemo(() => {
    if (!selectedSessionId) {
      return { tone: "neutral" as const, label: "未选择会话" };
    }
    if (realtimeState === "open") {
      return { tone: "success" as const, label: "实时已连接" };
    }
    if (shouldUseFallbackPolling) {
      return { tone: "warning" as const, label: "实时降级：10s轮询" };
    }
    if (!websocketEnabled) {
      return { tone: "neutral" as const, label: "实时未启用" };
    }
    if (realtimeState === "connecting") {
      return { tone: "info" as const, label: "实时连接中" };
    }
    return { tone: "warning" as const, label: "实时通道中断" };
  }, [realtimeState, selectedSessionId, shouldUseFallbackPolling, websocketEnabled]);
  const websocketUrl = useMemo(
    () => buildBackendWsUrl(`/ws/thinking-trace/${selectedSessionId || "pending"}`),
    [selectedSessionId],
  );
  const handleRealtimeEvent = useCallback(
    (event: WSEvent) => {
      applyRealtimeEvent(event);
    },
    [applyRealtimeEvent],
  );
  const ws = useWebSocket(websocketUrl, handleRealtimeEvent, {
    enabled: websocketEnabled,
    maxBufferedMessages: 400,
    getToken: () => getAccessTokenSync(),
    onAuthFailure: async () => {
      await recoverAuthSession();
    },
    shouldReconnect: () => getAuthRecoveryState() !== "terminal",
  });
  const previousWsStateRef = useRef<"connecting" | "open" | "closed" | "error">("closed");
  useEffect(() => {
    setRealtimeState(websocketEnabled ? ws.state : "closed");
  }, [setRealtimeState, websocketEnabled, ws.state]);
  useEffect(() => {
    const previousState = previousWsStateRef.current;
    if (
      websocketEnabled &&
      ws.state === "open" &&
      previousState !== "open" &&
      selectedSessionId
    ) {
      void reconcileEvents(selectedSessionId);
    }
    previousWsStateRef.current = ws.state;
  }, [reconcileEvents, selectedSessionId, websocketEnabled, ws.state]);
  useEffect(() => {
    if (
      !shouldUseFallbackPolling ||
      !selectedSessionId ||
      typeof window === "undefined"
    ) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void reconcileEvents(selectedSessionId);
    }, REALTIME_FALLBACK_POLL_MS);
    void reconcileEvents(selectedSessionId);
    return () => {
      window.clearInterval(timer);
    };
  }, [reconcileEvents, selectedSessionId, shouldUseFallbackPolling]);

  const pendingApprovalCount = records.filter((record) => {
    const status = String(record.overview?.progress.status ?? record.summary.status ?? "").trim().toLowerCase();
    return status === "approval_required" || status === "awaiting_approval";
  }).length;
  const runningCount = records.filter((record) => String(record.overview?.progress.status ?? record.summary.status ?? "").trim().toLowerCase() === "remediating").length;
  const canaryEnabledCount = records.filter((record) => Boolean(record.overview?.plan.canary?.enabled)).length;

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

  const handleSelectStep = useCallback((sessionId: string, stepId: number) => {
    setActiveStepSelection((current) =>
      current?.sessionId === sessionId && current.stepId === stepId ? null : { sessionId, stepId },
    );
  }, []);

  return (
    <div className="page-grid remediation-page">
      <section className="page-stage remediation-page__stage">
        <div className="page-stage__summary">
          <div className="card-grid--metrics remediation-page__metrics">
                  <MetricTile hint="已识别到修复流程的诊断会话" label="修复记录" value={records.length} />
                  <MetricTile hint="等待人工批准后进入执行" label="待审批" value={pendingApprovalCount} />
                  <MetricTile hint="正在执行或观察验证中" label="执行中" value={runningCount} />
                  <MetricTile hint="已配置灰度或金丝雀策略" label="金丝雀方案" value={canaryEnabledCount} />
                </div>
        </div>

        <div className="remediation-layout">
        <aside className="remediation-sidebar">
          <SurfaceCard className="page-stage__panel" title="修复记录" description="按状态和关键词快速定位会话，点击行后在右侧查看详情。" variant="panel">
            <div className="page-stack remediation-sidebar__body">
              <div className="page-stage__toolbar remediation-toolbar">
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
                <div className="status-row">
                  <StatusChip tone={realtimeChip.tone}>{realtimeChip.label}</StatusChip>
                  {lastEventId ? (
                    <span className="remediation-record-table__status-meta">{`last_event_id=${lastEventId}`}</span>
                  ) : null}
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

              <div className="page-stage__table-shell remediation-record-table-shell">
                <table className="remediation-record-table">
                  <thead>
                    <tr>
                      <th>修复记录</th>
                      <th>状态</th>
                      <th>修复内容</th>
                      <th>审批人</th>
                      <th>开始修复</th>
                      <th>进度</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRecords.map((record) => {
                      const recordOverview = record.overview;
                      const currentStatus = String(recordOverview?.progress.status ?? record.summary.status ?? "pending").trim();
                      const approver = getApprover(recordOverview?.timeline) ?? (recordOverview?.approval_required ? "待审批" : "未记录");
                      const startedAt = getExecutionStartedAt(recordOverview?.timeline);
                      const totalProgress = getRemediationOverallProgressDisplay(recordOverview);
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
                              <div className="remediation-record-table__status-stack">
                                <StatusChip tone={getStatusTone(currentStatus)}>{getStatusLabel(currentStatus)}</StatusChip>
                                {recordOverview?.plan.confidence ? <span className="remediation-record-table__status-meta">{`置信度 ${formatPercent(recordOverview.plan.confidence)}`}</span> : null}
                              </div>
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
                                <span>{totalProgress}%</span>
                                <div className="progress-track remediation-progress-track">
                                  <div className="progress-track__fill remediation-progress-track__fill" style={{ width: `${totalProgress}%` }} />
                                </div>
                              </div>
                            </td>
                          </tr>
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
      </section>

      <RemediationDetailDrawer
        actionLoading={actionLoading}
        activeStepSelection={activeStepSelection}
        events={events}
        isLoading={isLoading}
        onClose={() => setSelectedSessionId("")}
        onOpenApproval={() => setApprovalDialogOpen(true)}
        onSelectStep={handleSelectStep}
        open={Boolean(selectedRecord)}
        overview={overview}
        record={selectedRecord}
      />
      <ApprovalDialog
        onApprove={() => void handleSubmitApproval(true)}
        onCancel={() => setApprovalDialogOpen(false)}
        onReject={() => void handleSubmitApproval(false)}
        open={approvalDialogOpen}
        plan={selectedRecord?.summary.session_id === overview?.session_id ? overview?.plan : selectedRecord?.overview?.plan}
      />
    </div>
  );
}

export default RemediationPage;
