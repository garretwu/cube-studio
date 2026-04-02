import { Tooltip } from "antd";

import type { DiagnosisBootstrapAlertItem, DiagnosisBootstrapImpact, DiagnosisSession } from "../api/types";
import { formatSeverity } from "../utils/display";
import { formatDateTime, formatDurationSeconds } from "../utils/format";
import { StatusChip } from "./ui";

type DiagnosisSessionLike = DiagnosisSession & {
  summary?: string | null;
  started_at?: string | null;
};

function formatRoundLabel(round?: number | null) {
  return round && round > 0 ? `\u7b2c ${round} \u8f6e` : "\u9996\u8f6e";
}

function formatStageLabel(status?: string | null) {
  const labels: Record<string, string> = {
    approved: "\u5df2\u5ba1\u6279",
    awaiting_approval: "\u5f85\u5ba1\u6279",
    closed: "\u5df2\u5173\u95ed",
    diagnosed: "\u5df2\u8bca\u65ad",
    diagnosing: "\u8bca\u65ad\u4e2d",
    error: "\u5f02\u5e38",
    escalated: "\u5df2\u5347\u7ea7",
    failed: "\u5931\u8d25",
    proposed_fix_ready: "\u5f85\u6267\u884c\u4fee\u590d",
    re_diagnosed: "\u91cd\u65b0\u8bca\u65ad",
    remediating: "\u4fee\u590d\u4e2d",
    resolved: "\u5df2\u89e3\u51b3",
    running: "\u8fd0\u884c\u4e2d",
    testing: "\u9a8c\u8bc1\u4e2d",
    validating: "\u6821\u9a8c\u4e2d",
  };

  if (!status) {
    return "\u672a\u77e5";
  }

  return labels[status] ?? status.replace(/[_-]/g, " ");
}

function getPrimaryAlert(session: DiagnosisSessionLike) {
  return session.alert ?? null;
}

function resolveSessionName(session: DiagnosisSessionLike) {
  return session.bootstrap?.session_name?.trim() || getPrimaryAlert(session)?.alert_name || session.summary || "\u5f53\u524d\u4f1a\u8bdd";
}

function resolveStartedAt(session: DiagnosisSessionLike) {
  return session.bootstrap?.started_at ?? getPrimaryAlert(session)?.starts_at ?? session.started_at ?? null;
}

function resolveAlertSummary(session: DiagnosisSessionLike) {
  const relatedAlerts = session.bootstrap?.related_alerts;
  if (relatedAlerts?.items?.length) {
    return { count: relatedAlerts.count ?? relatedAlerts.items.length, items: relatedAlerts.items };
  }

  const alert = getPrimaryAlert(session);
  if (!alert) {
    return { count: 0, items: [] as DiagnosisBootstrapAlertItem[] };
  }

  return {
    count: relatedAlerts?.count ?? 1,
    items: [
      {
        id: alert.fingerprint,
        alert_name: alert.alert_name,
        severity: alert.severity,
        source_entity: alert.annotations.entity ?? alert.labels.instance ?? alert.labels.node ?? alert.labels.service ?? null,
        starts_at: alert.starts_at,
        summary: alert.annotations.summary ?? null,
      } satisfies DiagnosisBootstrapAlertItem,
    ],
  };
}

function resolveImpact(session: DiagnosisSessionLike): DiagnosisBootstrapImpact | null {
  if (session.bootstrap?.impact) {
    return session.bootstrap.impact;
  }

  const objectCount = session.diagnosis_result?.root_cause_entities?.length ?? 0;
  const serviceCount = session.diagnosis_result?.affected_services?.length ?? 0;

  if (!objectCount && !serviceCount) {
    return null;
  }

  return {
    object_count: objectCount,
    service_count: serviceCount,
    affected_entities: session.diagnosis_result?.root_cause_entities ?? [],
    affected_services: session.diagnosis_result?.affected_services ?? [],
    blast_radius_summary: session.diagnosis_result?.impact_summary,
  };
}

function renderSessionTooltip(sessionName: string, sessionId: string, startedAt: string | null) {
  return (
    <div className="diagnosis-session-summary__tooltip-list">
      <article className="diagnosis-session-summary__tooltip-item">
        <strong>{sessionName}</strong>
        <p>{sessionId}</p>
        {startedAt ? (
          <div className="diagnosis-session-summary__tooltip-meta">
            <span>{formatDateTime(startedAt)}</span>
          </div>
        ) : null}
      </article>
    </div>
  );
}

function renderAlertTooltip(items: DiagnosisBootstrapAlertItem[]) {
  if (!items.length) {
    return <span>\u6682\u65e0\u5173\u8054\u544a\u8b66\u4fe1\u606f</span>;
  }

  return (
    <div className="diagnosis-session-summary__tooltip-list">
      {items.map((item, index) => (
        <article key={item.id ?? `${item.alert_name}-${index}`} className="diagnosis-session-summary__tooltip-item">
          <div className="diagnosis-session-summary__tooltip-head">
            <strong>{item.alert_name}</strong>
            <span>{formatSeverity(item.severity)}</span>
          </div>
          {item.summary ? <p>{item.summary}</p> : null}
          <div className="diagnosis-session-summary__tooltip-meta">
            {item.source_entity ? <span>{item.source_entity}</span> : null}
            {item.starts_at ? <span>{formatDateTime(item.starts_at)}</span> : null}
          </div>
        </article>
      ))}
    </div>
  );
}

function renderImpactTooltip(impact: DiagnosisBootstrapImpact) {
  return (
    <div className="diagnosis-session-summary__tooltip-list">
      {impact.blast_radius_summary ? (
        <article className="diagnosis-session-summary__tooltip-item">
          <strong>\u5f71\u54cd\u6458\u8981</strong>
          <p>{impact.blast_radius_summary}</p>
        </article>
      ) : null}
      {impact.affected_entities?.length ? (
        <article className="diagnosis-session-summary__tooltip-item">
          <strong>\u5f71\u54cd\u5bf9\u8c61</strong>
          <p>{impact.affected_entities.join(" / ")}</p>
        </article>
      ) : null}
      {impact.affected_services?.length ? (
        <article className="diagnosis-session-summary__tooltip-item">
          <strong>\u5f71\u54cd\u670d\u52a1</strong>
          <p>{impact.affected_services.join(" / ")}</p>
        </article>
      ) : null}
    </div>
  );
}

type DiagnosisSessionSummaryCardProps = {
  session: DiagnosisSession;
};

function DiagnosisSessionSummaryCard({ session }: DiagnosisSessionSummaryCardProps) {
  const safeSession = session as DiagnosisSessionLike;
  const sessionName = resolveSessionName(safeSession);
  const startedAt = resolveStartedAt(safeSession);
  const alertSummary = resolveAlertSummary(safeSession);
  const impact = resolveImpact(safeSession);
  const durationLabel = formatDurationSeconds(session.duration_seconds);

  return (
    <div className="status-row diagnosis-session-chips">
      <Tooltip placement="bottomLeft" title={renderSessionTooltip(sessionName, session.session_id, startedAt)}>
        <span>
          <StatusChip className="diagnosis-session-chip diagnosis-session-chip--interactive" tone="accent">
            {`\u4f1a\u8bdd ${session.session_id}`}
          </StatusChip>
        </span>
      </Tooltip>

      <StatusChip className="diagnosis-session-chip" tone="info">
        {formatRoundLabel(session.re_diagnosis_round)}
      </StatusChip>

      <StatusChip className="diagnosis-session-chip" tone="success">
        {formatStageLabel(session.status)}
      </StatusChip>

      <Tooltip placement="bottomLeft" title={renderAlertTooltip(alertSummary.items)}>
        <span>
          <StatusChip className="diagnosis-session-chip diagnosis-session-chip--interactive" tone="warning">
            {alertSummary.count > 0 ? `${alertSummary.count} \u6761\u544a\u8b66` : "\u65e0\u544a\u8b66"}
          </StatusChip>
        </span>
      </Tooltip>

      <Tooltip placement="bottomLeft" title={impact ? renderImpactTooltip(impact) : "\u6682\u65e0\u5f71\u54cd\u8303\u56f4\u4fe1\u606f"}>
        <span>
          <StatusChip className="diagnosis-session-chip diagnosis-session-chip--interactive" tone="neutral">
            {impact ? `${impact.object_count} \u4e2a\u5bf9\u8c61 / ${impact.service_count} \u4e2a\u670d\u52a1` : "\u6682\u65e0\u5f71\u54cd"}
          </StatusChip>
        </span>
      </Tooltip>

      {durationLabel !== "--" ? (
        <StatusChip className="diagnosis-session-chip" tone="neutral">
          {durationLabel}
        </StatusChip>
      ) : null}
    </div>
  );
}

export default DiagnosisSessionSummaryCard;
