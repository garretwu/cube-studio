import { useEffect, useMemo, useRef, useState } from "react";
import { Select } from "antd";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import type { AlertStatus, DiagnosisSession, DiagnosisSessionSummary, Severity } from "../api/types";
import { AppButton, AppIcon, AppInput, MetricTile, StatusChip, SurfaceCard } from "../components/ui";
import { useAlertStore } from "../store/alertStore";
import { useDiagnosisStore } from "../store/diagnosisStore";
import { buildAlertIncidentKey } from "../utils/alerts";
import { formatSeverity } from "../utils/display";
import { formatPercent, formatTimestamp } from "../utils/format";
import {
  buildAlertDashboardView,
  getAlertDashboardSearchText,
  type AlertDashboardItem,
  type AlertDashboardStatusKey,
  type ChipTone,
} from "./alertsModifiedModel";

type DashboardStatusFilter = "all" | AlertDashboardStatusKey;

function severityTone(severity: Severity): ChipTone {
  switch (severity) {
    case "critical":
      return "danger";
    case "warning":
      return "warning";
    default:
      return "info";
  }
}

function statusFilterLabel(value: DashboardStatusFilter) {
  switch (value) {
    case "pending_diagnosis":
      return "待诊断";
    case "diagnosing":
      return "诊断中/执行中";
    case "pending_remediation":
      return "待审批/待执行";
    case "resolved":
      return "已恢复";
    case "attention":
      return "已转人工/异常结束";
    default:
      return "全部状态";
  }
}

function actionVariant(item: AlertDashboardItem): "primary" | "secondary" {
  return item.action.kind === "diagnose" || item.action.kind === "remediation" ? "primary" : "secondary";
}

function AlertsModifiedPage() {
  const navigate = useNavigate();
  const { alerts, clusters, fetchAlerts, isLoading, error: alertsError } = useAlertStore();
  const [query, setQuery] = useState("");
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");
  const [statusFilter, setStatusFilter] = useState<DashboardStatusFilter>("all");
  const [historySummaries, setHistorySummaries] = useState<DiagnosisSessionSummary[]>([]);
  const [sessionDetails, setSessionDetails] = useState<Record<string, DiagnosisSession | undefined>>({});
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [diagnosingItemId, setDiagnosingItemId] = useState<string | null>(null);
  const [diagnosisError, setDiagnosisError] = useState<string | null>(null);
  const attemptedSessionIdsRef = useRef(new Set<string>());

  useEffect(() => {
    void fetchAlerts();
  }, [fetchAlerts]);

  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    setHistoryError(null);

    void apiClient
      .getDiagnosisHistorySessions()
      .then((summaries) => {
        if (cancelled) {
          return;
        }
        setHistorySummaries(summaries);
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setHistorySummaries([]);
        setHistoryError(error instanceof Error ? error.message : "诊断会话同步失败");
      })
      .finally(() => {
        if (!cancelled) {
          setHistoryLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const matchingSummaries = useMemo(() => {
    const activeIncidentKeys = new Set(alerts.map((alert) => buildAlertIncidentKey(alert)));
    return historySummaries.filter((summary) => {
      const incidentKey = String(summary.incident_key ?? "").trim();
      return incidentKey.length > 0 && activeIncidentKeys.has(incidentKey);
    });
  }, [alerts, historySummaries]);

  useEffect(() => {
    const pendingSummaries = matchingSummaries.filter((summary) => {
      if (!summary.session_id) {
        return false;
      }

      if (Object.prototype.hasOwnProperty.call(sessionDetails, summary.session_id)) {
        return false;
      }

      if (attemptedSessionIdsRef.current.has(summary.session_id)) {
        return false;
      }

      return true;
    });

    if (pendingSummaries.length === 0) {
      return;
    }

    pendingSummaries.forEach((summary) => attemptedSessionIdsRef.current.add(summary.session_id));
    let cancelled = false;

    void Promise.allSettled(
      pendingSummaries.map(async (summary) => ({
        sessionId: summary.session_id,
        session: await apiClient.getDiagnosisSession(summary.session_id),
      })),
    ).then((results) => {
      if (cancelled) {
        return;
      }

      const nextDetails: Record<string, DiagnosisSession | undefined> = {};
      results.forEach((result, index) => {
        const sessionId = pendingSummaries[index]?.session_id;
        if (!sessionId) {
          return;
        }

        if (result.status === "fulfilled") {
          nextDetails[sessionId] = result.value.session ?? undefined;
          return;
        }

        nextDetails[sessionId] = undefined;
      });

      setSessionDetails((current) => ({ ...current, ...nextDetails }));
    });

    return () => {
      cancelled = true;
    };
  }, [matchingSummaries, sessionDetails]);

  const view = useMemo(
    () => buildAlertDashboardView(alerts, clusters, historySummaries, sessionDetails),
    [alerts, clusters, historySummaries, sessionDetails],
  );

  const filteredItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    return view.items.filter((item) => {
      const matchesSeverity = severityFilter === "all" || item.severity === severityFilter;
      const matchesStatus = statusFilter === "all" || item.statusKey === statusFilter;
      const matchesQuery = !normalizedQuery || getAlertDashboardSearchText(item).includes(normalizedQuery);
      return matchesSeverity && matchesStatus && matchesQuery;
    });
  }, [query, severityFilter, statusFilter, view.items]);

  const detailLoading = matchingSummaries.some(
    (summary) => summary.session_id && !Object.prototype.hasOwnProperty.call(sessionDetails, summary.session_id),
  );

  const visibleError = diagnosisError ?? alertsError ?? historyError;

  const startDiagnosis = async (item: AlertDashboardItem) => {
    const candidates = alerts.filter((alert) => buildAlertIncidentKey(alert) === item.incidentKey);
    if (candidates.length === 0) {
      setDiagnosisError("未找到可用于诊断的告警事件，请先刷新告警数据。");
      return;
    }

    const sorted = [...candidates].sort((left, right) => {
      const severityPriority = { critical: 3, warning: 2, info: 1 } as const;
      const statusPriority = (status: AlertStatus) => (status === "firing" ? 2 : status === "resolved" ? 1 : 0);
      const statusGap = statusPriority(right.status) - statusPriority(left.status);
      if (statusGap !== 0) {
        return statusGap;
      }

      const severityGap = severityPriority[right.severity] - severityPriority[left.severity];
      if (severityGap !== 0) {
        return severityGap;
      }

      return new Date(right.starts_at).getTime() - new Date(left.starts_at).getTime();
    });

    const selected = sorted[0];
    if (!selected) {
      setDiagnosisError("未找到可用于诊断的告警事件，请先刷新告警数据。");
      return;
    }

    setDiagnosisError(null);
    setDiagnosingItemId(item.id);
    const extraFingerprints = item.fingerprints.filter((fingerprint) => fingerprint !== selected.fingerprint);
    const fps = extraFingerprints.length > 0 ? extraFingerprints : undefined;
    try {
      const pendingSessionId = useDiagnosisStore.getState().startStreamingDiagnosis(
        selected,
        fps,
        (sessionId) => {
          navigate(`/diagnosis/${sessionId}`, { replace: true });
        },
      );
      navigate(`/diagnosis/${pendingSessionId}`);
    } catch {
      setDiagnosisError("发起流式诊断失败，请稍后重试。");
      setDiagnosingItemId(null);
    }
  };

  const handleItemAction = (item: AlertDashboardItem) => {
    if (item.action.kind === "diagnose") {
      void startDiagnosis(item);
      return;
    }

    navigate(item.action.path);
  };

  return (
    <div className="page-grid alerts-dashboard-page">
      <h1 className="visually-hidden">告警诊断</h1>
      <section className="page-stage alerts-dashboard-stage">
        <div className="page-stage__summary">
          <div className="card-grid--metrics alerts-dashboard-metrics">
            <MetricTile
              hint="当前告警快照中的诊断工作项"
              icon="notification"
              label="告警工作项"
              tone="accent"
              value={view.metrics.totalItems}
            />
            <MetricTile
              hint="已命中诊断或正在执行中的工作项"
              icon="chart"
              label="诊断中"
              tone="info"
              value={view.metrics.diagnosingCount}
            />
            <MetricTile
              hint="等待审批或待进入修复流程"
              icon="clipboardTasks"
              label="待审批/待执行"
              tone="warning"
              value={view.metrics.pendingActionCount}
            />
            <MetricTile
              hint="按当前快照口径统计今日已恢复项"
              icon="checkmarkCircle"
              label="今日自动恢复"
              tone="success"
              value={view.metrics.resolvedTodayCount}
            />
          </div>
        </div>

        <SurfaceCard
          actions={
            <div className="alerts-dashboard-workbench__actions">
              <StatusChip tone="neutral">{`${filteredItems.length} / ${view.items.length} 项`}</StatusChip>
            </div>
          }
          bodyClassName="alerts-dashboard-workbench__body"
          className="page-stage__panel alerts-dashboard-workbench"
          description="筛选与浏览当前告警诊断工作项。"
          title="告警诊断"
          variant="panel"
        >
          <div className="page-stage__toolbar alerts-dashboard-toolbar">
            <AppInput
              className="alerts-dashboard-toolbar__search"
              onChange={setQuery}
              placeholder="搜索告警名、实体、根因、服务或 session ID"
              prefix={<AppIcon name="search" size={16} />}
              value={query}
            />
            <Select
              className="app-select alerts-dashboard-toolbar__select"
              onChange={(value) => setSeverityFilter(value as Severity | "all")}
              options={[
                { value: "all", label: formatSeverity("all") },
                { value: "critical", label: formatSeverity("critical") },
                { value: "warning", label: formatSeverity("warning") },
                { value: "info", label: formatSeverity("info") },
              ]}
              value={severityFilter}
            />
            <Select
              className="app-select alerts-dashboard-toolbar__select"
              onChange={(value) => setStatusFilter(value as DashboardStatusFilter)}
              options={[
                { value: "all", label: statusFilterLabel("all") },
                { value: "pending_diagnosis", label: statusFilterLabel("pending_diagnosis") },
                { value: "diagnosing", label: statusFilterLabel("diagnosing") },
                { value: "pending_remediation", label: statusFilterLabel("pending_remediation") },
                { value: "resolved", label: statusFilterLabel("resolved") },
                { value: "attention", label: statusFilterLabel("attention") },
              ]}
              value={statusFilter}
            />
          </div>
          {visibleError ? (
            <div className="state-block alerts-dashboard-workbench__error">
              <p className="data-list__copy">{visibleError}</p>
            </div>
          ) : null}
          {filteredItems.length > 0 ? (
            <div className="page-stage__table-shell alerts-dashboard-table-shell">
              <table className="alerts-dashboard-table">
                <thead>
                  <tr>
                    <th>告警簇 / 级别</th>
                    <th>根节点 / 影响对象</th>
                    <th>Auto-SRE 状态</th>
                    <th>AI 根因分析 & 修复计划</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.map((item) => {
                    const showLoadingAnalysis = item.hasSession && !item.isSessionDetailLoaded;

                    return (
                      <tr key={item.id} className="alerts-dashboard-table__row">
                        <td className="alerts-dashboard-table__cell alerts-dashboard-table__cell--title">
                          <div className="status-row alerts-dashboard-table__chips">
                            <StatusChip tone={severityTone(item.severity)}>{formatSeverity(item.severity)}</StatusChip>
                          </div>
                          <p className="alerts-dashboard-table__title">{item.title}</p>
                          <p className="alerts-dashboard-table__summary">{item.summary}</p>
                          <div className="alerts-dashboard-table__meta">
                            <span>{`最新告警 ${formatTimestamp(item.latestStartsAt)}`}</span>
                            <span>{`incident_key ${item.incidentKey}`}</span>
                          </div>
                        </td>
                        <td className="alerts-dashboard-table__cell alerts-dashboard-table__cell--entity">
                          <p className="alerts-dashboard-table__entity">{item.rootEntity}</p>
                          <p className="alerts-dashboard-table__impact">{item.impactScope}</p>
                        </td>
                        <td className="alerts-dashboard-table__cell alerts-dashboard-table__cell--status">
                          <div className="alerts-dashboard-table__status-block">
                            <StatusChip tone={item.statusTone}>{item.statusLabel}</StatusChip>
                            <p className="alerts-dashboard-table__status-copy">
                              {item.sessionId ? `会话 ${item.sessionId}` : "尚未创建诊断会话"}
                            </p>
                            <p className="alerts-dashboard-table__status-copy">
                              {item.sessionId
                                ? `状态更新时间 ${formatTimestamp(item.statusTimestamp)}`
                                : `告警时间 ${formatTimestamp(item.latestStartsAt)}`}
                            </p>
                          </div>
                        </td>
                        <td className="alerts-dashboard-table__cell alerts-dashboard-table__cell--analysis">
                          {item.analysisSummary ? (
                            <div className="alerts-dashboard-analysis">
                              <p className="alerts-dashboard-analysis__line">
                                <span className="alerts-dashboard-analysis__label">根因</span>
                                <span>{item.analysisSummary}</span>
                              </p>
                              {typeof item.confidence === "number" ? (
                                <StatusChip tone="neutral">{`置信度 ${formatPercent(item.confidence)}`}</StatusChip>
                              ) : null}
                              {item.planSummary ? (
                                <p className="alerts-dashboard-analysis__line">
                                  <span className="alerts-dashboard-analysis__label">计划</span>
                                  <span>{item.planSummary}</span>
                                </p>
                              ) : null}
                            </div>
                          ) : showLoadingAnalysis ? (
                            <p className="alerts-dashboard-analysis__empty">诊断详情同步中...</p>
                          ) : (
                            <p className="alerts-dashboard-analysis__empty">尚未生成诊断结论</p>
                          )}
                        </td>
                        <td className="alerts-dashboard-table__cell alerts-dashboard-table__cell--action">
                          <AppButton
                            disabled={diagnosingItemId !== null && item.action.kind === "diagnose"}
                            onClick={() => handleItemAction(item)}
                            size="sm"
                            variant={actionVariant(item)}
                          >
                            {diagnosingItemId === item.id ? "诊断发起中..." : item.action.label}
                          </AppButton>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="state-block alerts-dashboard-empty">
              <p className="mini-card__title">当前没有可展示的告警工作项</p>
              <p className="mini-card__copy">可以尝试放宽筛选条件，或等待新的告警快照同步完成。</p>
            </div>
          )}
        </SurfaceCard>
      </section>
    </div>
  );
}

export default AlertsModifiedPage;
