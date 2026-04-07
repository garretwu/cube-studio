import { useEffect, useMemo, useState } from "react";
import { Select } from "antd";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import type { AlertStatus, DiagnosisSession, Severity } from "../api/types";
import { AppButton, AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useAlertStore } from "../store/alertStore";
import { formatSeverity } from "../utils/display";
import { formatTimestamp } from "../utils/format";
import { buildAlertConvergenceView } from "./alertsModifiedModel";

function severityTone(severity: Severity) {
  switch (severity) {
    case "critical":
      return "danger";
    case "warning":
      return "warning";
    default:
      return "info";
  }
}

function alertStatusTone(status: AlertStatus) {
  switch (status) {
    case "firing":
      return "danger";
    case "resolved":
      return "success";
    default:
      return "neutral";
  }
}

function formatAlertStatus(status: AlertStatus) {
  switch (status) {
    case "firing":
      return "持续告警";
    case "resolved":
      return "已恢复";
    default:
      return "已静默";
  }
}

function websocketTone(state: "connecting" | "open" | "closed" | "error") {
  switch (state) {
    case "open":
      return "success";
    case "connecting":
      return "warning";
    case "error":
      return "danger";
    default:
      return "neutral";
  }
}

function websocketLabel(state: "connecting" | "open" | "closed" | "error") {
  switch (state) {
    case "open":
      return "WS 已连接";
    case "connecting":
      return "WS 连接中";
    case "error":
      return "WS 异常";
    default:
      return "WS 未连接";
  }
}

function AlertsModifiedPage() {
  const navigate = useNavigate();
  const {
    alerts,
    clusters,
    fetchAlerts,
    isLoading,
    wsState,
    lastSnapshotSyncAt,
    realtimeEnabled,
  } = useAlertStore();
  const [query, setQuery] = useState("");
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");
  const [activeSession, setActiveSession] = useState<DiagnosisSession | undefined>();
  const [diagnosingResultId, setDiagnosingResultId] = useState<string | null>(null);
  const [diagnosisError, setDiagnosisError] = useState<string | null>(null);
  const [expandedFingerprint, setExpandedFingerprint] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    void fetchAlerts();
    void apiClient
      .getDiagnosisSession()
      .then((session) => {
        if (!cancelled) {
          setActiveSession(session ?? undefined);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setActiveSession(undefined);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchAlerts]);

  const filteredAlerts = useMemo(
    () =>
      alerts.filter((alert) => {
        const matchesSeverity = severityFilter === "all" || alert.severity === severityFilter;
        const haystack = [
          alert.alert_name,
          alert.annotations.summary,
          alert.annotations.entity,
          alert.labels.instance,
          alert.labels.node,
          alert.labels.service,
          alert.fingerprint,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();

        const matchesQuery = !query.trim() || haystack.includes(query.trim().toLowerCase());
        return matchesSeverity && matchesQuery;
      }),
    [alerts, query, severityFilter],
  );

  const { results, flow } = useMemo(
    () => buildAlertConvergenceView(filteredAlerts, clusters, activeSession),
    [activeSession, clusters, filteredAlerts],
  );

  useEffect(() => {
    setExpandedFingerprint((current) => {
      if (flow.length === 0) {
        return null;
      }

      if (current && flow.some((group) => group.fingerprint === current)) {
        return current;
      }

      return flow[0].fingerprint;
    });
  }, [flow]);

  const mergedCount = results.filter((result) => result.route.label === "并入已有 Session").length;
  const createCount = results.filter((result) => result.route.label === "将创建 Session").length;
  const observeCount = results.filter((result) => result.route.label === "观察中").length;
  const fingerprintCount = flow.length;
  const foldedEventCount = results.reduce((total, result) => total + result.duplicateFoldedCount, 0);

  const startDiagnosisFromResult = async (resultId: string, fingerprint: string) => {
    const candidates = filteredAlerts.filter((item) => item.fingerprint === fingerprint);
    if (!candidates.length) {
      setDiagnosisError("未找到可用于诊断的告警事件，请先刷新告警数据。");
      return;
    }
    const sorted = [...candidates].sort((left, right) => {
      const severityRank = { critical: 3, warning: 2, info: 1 } as const;
      const statusRank = (status: AlertStatus) => (status === "firing" ? 2 : status === "resolved" ? 1 : 0);
      const statusGap = statusRank(right.status) - statusRank(left.status);
      if (statusGap !== 0) {
        return statusGap;
      }
      const severityGap = severityRank[right.severity] - severityRank[left.severity];
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
    setDiagnosingResultId(resultId);
    try {
      const session = await apiClient.startDiagnoseAlert(selected);
      setActiveSession(session);
      navigate(`/diagnosis/${session.session_id}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "发起诊断失败";
      setDiagnosisError(message);
    } finally {
      setDiagnosingResultId(null);
    }
  };

  return (
    <div className="page-grid alerts-convergence-page">
      <SectionHeader
        actions={
          <div className="status-row">
            <StatusChip tone="accent">{`${results.length} 个结果`}</StatusChip>
            <StatusChip tone="danger">{`${filteredAlerts.length} 条事件`}</StatusChip>
            <StatusChip tone="neutral">{`${fingerprintCount} 个 fingerprint`}</StatusChip>
            <StatusChip tone="neutral">
              {activeSession ? `当前 Session ${activeSession.session_id}` : "暂无已绑定 Session"}
            </StatusChip>
          </div>
        }
        eyebrow="告警（修改）"
        title="收敛结果看板"
      />

      <SurfaceCard bodyClassName="alerts-convergence-toolbar-card__body" className="alerts-convergence-toolbar-card">
        <div className="input-row alerts-convergence-toolbar">
          <Select
            className="app-select"
            onChange={(value) => setSeverityFilter(value as Severity | "all")}
            options={[
              { value: "all", label: formatSeverity("all") },
              { value: "critical", label: formatSeverity("critical") },
              { value: "warning", label: formatSeverity("warning") },
              { value: "info", label: formatSeverity("info") },
            ]}
            value={severityFilter}
          />
          <AppInput
            onChange={setQuery}
            placeholder="按告警名、实体、服务或 fingerprint 过滤"
            prefix={<AppIcon name="search" size={16} />}
            value={query}
          />
          <StatusChip tone={isLoading ? "warning" : "success"}>{isLoading ? "同步中" : "已同步"}</StatusChip>
        </div>

        <div className="status-row alerts-convergence-toolbar__summary">
          <StatusChip tone="accent">{`压缩 ${foldedEventCount} 条重复事件`}</StatusChip>
          <StatusChip tone="accent">{`${mergedCount} 个并入已有 Session`}</StatusChip>
          <StatusChip tone="danger">{`${createCount} 个将创建 Session`}</StatusChip>
          <StatusChip tone="warning">{`${observeCount} 个观察中`}</StatusChip>
          <StatusChip tone={realtimeEnabled ? websocketTone(wsState) : "neutral"}>
            {realtimeEnabled ? websocketLabel(wsState) : "实时同步未启用"}
          </StatusChip>
          <StatusChip tone="neutral">{`快照 ${formatTimestamp(lastSnapshotSyncAt)}`}</StatusChip>
        </div>
        {diagnosisError ? (
          <div className="state-block">
            <p className="data-list__copy">{diagnosisError}</p>
          </div>
        ) : null}
      </SurfaceCard>

      <div className="alerts-convergence-layout">
        <SurfaceCard
          actions={<StatusChip tone="neutral">{`${fingerprintCount} 个 fingerprint`}</StatusChip>}
          className="alerts-convergence-panel alerts-convergence-side"
          title="原始告警流"
        >
          {flow.length > 0 ? (
            <div className="alerts-stream-list">
              {flow.map((group) => {
                const isExpanded = expandedFingerprint === group.fingerprint;

                return (
                  <article key={group.fingerprint} className="alerts-stream-group">
                    <button
                      aria-expanded={isExpanded}
                      aria-label={`切换 ${group.fingerprint} 事件流`}
                      className="alerts-stream-group__toggle"
                      onClick={() => setExpandedFingerprint(isExpanded ? null : group.fingerprint)}
                      type="button"
                    >
                      <div className="alerts-stream-group__header">
                        <div className="status-row alerts-stream-group__chips">
                          <StatusChip tone={severityTone(group.severity)}>{formatSeverity(group.severity)}</StatusChip>
                          <StatusChip tone={group.compressionTone}>{group.compressionLabel}</StatusChip>
                          <StatusChip tone={group.fingerprintTone}>{group.fingerprintRole}</StatusChip>
                        </div>
                        <span className="alerts-stream-group__expand">
                          {isExpanded ? "收起" : "展开"}
                          <AppIcon name="chevronDown" size={14} />
                        </span>
                      </div>

                      <div className="alerts-stream-group__main">
                        <div className="alerts-stream-group__titleblock">
                          <p className="alerts-stream-group__title">{group.alertName}</p>
                          <div className="alerts-stream-group__meta">
                            <span>
                              实体 <strong>{group.entity}</strong>
                            </span>
                            <span>
                              最新 <strong>{formatTimestamp(group.latestStartsAt)}</strong>
                            </span>
                            <span>
                              收敛到 <strong>{group.convergenceTarget}</strong>
                            </span>
                            <span>
                              指纹 <code className="alerts-stream-group__fingerprint">{group.fingerprint}</code>
                            </span>
                          </div>
                        </div>
                        <StatusChip className="alerts-stream-group__route" tone={group.routeTone}>
                          {group.routeLabel}
                        </StatusChip>
                      </div>
                    </button>

                    {isExpanded ? (
                      <div className="alerts-stream-group__events">
                        {group.events.map((event) => (
                          <div key={event.id} className="alerts-stream-event">
                            <div className="alerts-stream-event__head">
                              <div className="status-row alerts-stream-event__chips">
                                <StatusChip tone={severityTone(event.severity)}>{formatSeverity(event.severity)}</StatusChip>
                                <StatusChip tone={alertStatusTone(event.status)}>{formatAlertStatus(event.status)}</StatusChip>
                              </div>
                              <time className="alerts-stream-event__time">{formatTimestamp(event.startsAt)}</time>
                            </div>
                            <p className="alerts-stream-event__title">{event.alertName}</p>
                            <p className="alerts-stream-event__summary">{event.summary}</p>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="state-block">
              <p className="data-list__copy">当前筛选条件下没有匹配到原始告警。</p>
            </div>
          )}
        </SurfaceCard>

        <SurfaceCard
          actions={<StatusChip tone="accent">{`${results.length} 个最终结果`}</StatusChip>}
          className="alerts-convergence-panel alerts-convergence-main"
          title="收敛结果"
        >
          {results.length > 0 ? (
            <div className="convergence-result-list">
              {results.map((result) => {
                const visibleAlertNames = result.alertNames.slice(0, 2);
                const hiddenAlertNameCount = result.alertNames.length - visibleAlertNames.length;

                return (
                  <article key={result.id} className="convergence-result-card">
                    <div className="convergence-result-card__header">
                      <div className="convergence-result-card__headline">
                        <div className="status-row">
                          <StatusChip tone={severityTone(result.severity)}>{formatSeverity(result.severity)}</StatusChip>
                          <StatusChip tone={result.route.tone}>{result.route.label}</StatusChip>
                          <StatusChip tone="neutral">{`${result.alertCount} 条事件`}</StatusChip>
                          <StatusChip tone="neutral">{`${result.fingerprintCount} 个 fingerprint`}</StatusChip>
                        </div>
                        <h3 className="convergence-result-card__title">{result.title}</h3>
                        <p className="convergence-result-card__summary">{result.summary}</p>
                      </div>
                      <AppButton
                        iconRight="arrowRight"
                        onClick={() => {
                          if (result.route.label === "将创建 Session") {
                            void startDiagnosisFromResult(result.id, result.primaryFingerprint);
                            return;
                          }
                          navigate(result.route.path);
                        }}
                        size="sm"
                        variant="primary"
                        disabled={diagnosingResultId !== null}
                      >
                        {diagnosingResultId === result.id ? "诊断发起中..." : result.route.ctaLabel}
                      </AppButton>
                    </div>

                    <div className="convergence-result-card__facts">
                      <div className="convergence-result-card__fact">
                        <span className="convergence-result-card__fact-label">事件数</span>
                        <strong className="convergence-result-card__fact-value">{result.alertCount}</strong>
                      </div>
                      <div className="convergence-result-card__fact">
                        <span className="convergence-result-card__fact-label">指纹数</span>
                        <strong className="convergence-result-card__fact-value">{result.fingerprintCount}</strong>
                      </div>
                      <div className="convergence-result-card__fact">
                        <span className="convergence-result-card__fact-label">重复折叠</span>
                        <strong className="convergence-result-card__fact-value">{result.duplicateFoldedCount}</strong>
                      </div>
                      <div className="convergence-result-card__fact convergence-result-card__fact--wide">
                        <span className="convergence-result-card__fact-label">影响范围</span>
                        <strong className="convergence-result-card__fact-copy">{result.impactScope}</strong>
                      </div>
                    </div>

                    <div className="convergence-result-card__insights">
                      <div className="convergence-result-card__insight">
                        <span className="convergence-result-card__fact-label">主判断</span>
                        <p className="convergence-result-card__insight-copy">{result.primaryJudgment}</p>
                      </div>
                      <div className="convergence-result-card__insight">
                        <span className="convergence-result-card__fact-label">路由结果</span>
                        <p className="convergence-result-card__insight-copy">{result.route.note}</p>
                      </div>
                    </div>

                    <div className="convergence-result-card__footer">
                      <div className="status-row">
                        {visibleAlertNames.map((name) => (
                          <StatusChip key={name} tone="info">
                            {name}
                          </StatusChip>
                        ))}
                        {hiddenAlertNameCount > 0 ? <StatusChip tone="neutral">{`+${hiddenAlertNameCount}`}</StatusChip> : null}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="state-block">
              <p className="data-list__copy">当前筛选条件下没有可展示的收敛结果。</p>
            </div>
          )}
        </SurfaceCard>
      </div>
    </div>
  );
}

export default AlertsModifiedPage;

