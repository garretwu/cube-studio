import type { Alert, AlertCluster, DiagnosisSession, DiagnosisSessionSummary } from "../api/types";
import { formatWorkflowStatus } from "../utils/display";

export type ChipTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

export type AlertDashboardStatusKey =
  | "pending_diagnosis"
  | "diagnosing"
  | "pending_remediation"
  | "resolved"
  | "attention";

export type AlertDashboardAction = {
  kind: "diagnose" | "diagnosis" | "remediation";
  label: string;
  path: string;
};

export type AlertDashboardItem = {
  id: string;
  title: string;
  summary: string;
  severity: Alert["severity"];
  statusKey: AlertDashboardStatusKey;
  statusLabel: string;
  statusTone: ChipTone;
  statusTimestamp: string;
  sessionId?: string;

  latestStartsAt: string;
  primaryFingerprint: string;
  rootEntity: string;
  impactScope: string;
  alertNames: string[];
  analysisSummary?: string | null;
  confidence?: number | null;
  planSummary?: string | null;
  hasSession: boolean;
  isSessionDetailLoaded: boolean;
  action: AlertDashboardAction;
};

export type AlertDashboardMetrics = {
  totalItems: number;
  diagnosingCount: number;
  pendingActionCount: number;
  resolvedTodayCount: number;
};

export type AlertDashboardView = {
  items: AlertDashboardItem[];
  metrics: AlertDashboardMetrics;
};

type SessionDescriptor = {
  key: AlertDashboardStatusKey;
  label: string;
  tone: ChipTone;
};

const severityRank: Record<Alert["severity"], number> = {
  critical: 0,
  warning: 1,
  info: 2,
};

const statusRank: Record<AlertDashboardStatusKey, number> = {
  pending_remediation: 0,
  diagnosing: 1,
  pending_diagnosis: 2,
  attention: 3,
  resolved: 4,
};

const approvalStates = new Set(["approval_required", "awaiting_approval", "proposed_fix_ready"]);
const diagnosingStates = new Set(["diagnosing", "diagnosed", "approved", "remediating", "validating", "re_diagnosed"]);
const resolvedStates = new Set(["resolved"]);
const attentionStates = new Set(["closed", "escalated", "failed", "timeout", "rejected"]);

function unique(values: Array<string | null | undefined>) {
  return [...new Set(values.filter((value): value is string => Boolean(value)))];
}

function normalizeSentence(value: string) {
  return value.trim().replace(/[。；;，,\s]+$/u, "");
}

function normalizeStatusValue(value?: string | null) {
  return String(value ?? "").trim().toLowerCase();
}

function sortAlertsForPriority(left: Alert, right: Alert) {
  const leftFiring = left.status === "firing" ? 1 : 0;
  const rightFiring = right.status === "firing" ? 1 : 0;
  if (rightFiring !== leftFiring) {
    return rightFiring - leftFiring;
  }

  const severityGap = severityRank[left.severity] - severityRank[right.severity];
  if (severityGap !== 0) {
    return severityGap;
  }

  return new Date(right.starts_at).getTime() - new Date(left.starts_at).getTime();
}

function getAlertEntity(alert: Alert) {
  return (
    alert.annotations.entity ??
    alert.labels.instance ??
    alert.labels.node ??
    alert.labels.service ??
    alert.labels.aidc ??
    "未知实体"
  );
}

function highestSeverity(alerts: Alert[]): Alert["severity"] {
  if (alerts.some((alert) => alert.severity === "critical")) {
    return "critical";
  }

  if (alerts.some((alert) => alert.severity === "warning")) {
    return "warning";
  }

  return "info";
}

function pickPrimaryAlert(alerts: Alert[]) {
  return [...alerts].sort(sortAlertsForPriority)[0] ?? alerts[0];
}

function getLatestStartsAt(alerts: Alert[]) {
  return [...alerts]
    .sort((left, right) => new Date(right.starts_at).getTime() - new Date(left.starts_at).getTime())[0]?.starts_at ?? alerts[0]?.starts_at ?? new Date(0).toISOString();
}

function buildTitle(cluster: AlertCluster | undefined, alerts: Alert[]) {
  const fallbackTitle = normalizeSentence(cluster?.summary ?? "");
  return pickPrimaryAlert(alerts)?.alert_name ?? (fallbackTitle || "未命名告警");
}

function buildSummary(cluster: AlertCluster | undefined, alerts: Alert[]) {
  if (cluster?.summary?.trim()) {
    return `${normalizeSentence(cluster.summary)}。`;
  }

  const primaryAlert = pickPrimaryAlert(alerts);
  const primarySummary = primaryAlert?.annotations.summary?.trim();
  if (primarySummary) {
    return `${normalizeSentence(primarySummary)}。`;
  }

  const services = unique(alerts.map((alert) => alert.labels.service));
  if (services.length > 0) {
    return `当前主要影响服务：${services.slice(0, 2).join("、")}。`;
  }

  const entities = unique(alerts.map((alert) => getAlertEntity(alert)));
  if (entities.length > 0) {
    return `当前主要影响对象：${entities.slice(0, 2).join("、")}。`;
  }

  return "当前告警需要进一步诊断。";
}

function buildRootEntity(alerts: Alert[], session?: DiagnosisSession) {
  const rootCauseEntities = session?.diagnosis_result?.root_cause_entities?.filter(Boolean) ?? [];
  if (rootCauseEntities.length > 0) {
    return rootCauseEntities.length > 1
      ? `${rootCauseEntities[0]} 等 ${rootCauseEntities.length} 个对象`
      : rootCauseEntities[0] ?? "未知实体";
  }

  return getAlertEntity(pickPrimaryAlert(alerts) ?? alerts[0]);
}

function buildImpactScope(alerts: Alert[], session?: DiagnosisSession) {
  const impactSummary = session?.diagnosis_result?.impact_summary?.trim();
  if (impactSummary) {
    return impactSummary;
  }

  const services = unique([
    ...(session?.diagnosis_result?.affected_services ?? []),
    ...alerts.map((alert) => alert.labels.service),
  ]);
  const entities = unique([
    ...(session?.diagnosis_result?.root_cause_entities ?? []),
    ...alerts.map((alert) => getAlertEntity(alert)),
  ]);
  const parts: string[] = [];

  if (services.length > 0) {
    parts.push(`服务 ${services.slice(0, 2).join("、")}`);
  }

  if (entities.length > 0) {
    parts.push(`对象 ${entities.slice(0, 3).join("、")}`);
  }

  return parts.join(" · ") || "影响范围仍在持续观察";
}

function resolveSessionDescriptor(summary?: DiagnosisSessionSummary, session?: DiagnosisSession): SessionDescriptor {
  if (!summary) {
    return {
      key: "pending_diagnosis",
      label: "待诊断",
      tone: "warning",
    };
  }

  const outcome = normalizeStatusValue(summary.outcome);
  const status = normalizeStatusValue(session?.status ?? summary.status);
  const displayValue = approvalStates.has(status)
    ? status
    : approvalStates.has(outcome)
      ? outcome
      : outcome || status;

  if (approvalStates.has(status) || approvalStates.has(outcome)) {
    return {
      key: "pending_remediation",
      label: formatWorkflowStatus(displayValue || "awaiting_approval", "待审批/待执行"),
      tone: status === "approval_required" || status === "awaiting_approval" ? "info" : "accent",
    };
  }

  if (resolvedStates.has(status) || resolvedStates.has(outcome)) {
    return {
      key: "resolved",
      label: formatWorkflowStatus(displayValue || "resolved", "已恢复"),
      tone: "success",
    };
  }

  if (attentionStates.has(status) || attentionStates.has(outcome)) {
    return {
      key: "attention",
      label: formatWorkflowStatus(displayValue || status || outcome, "需关注"),
      tone: "danger",
    };
  }

  if (diagnosingStates.has(status) || diagnosingStates.has(outcome)) {
    return {
      key: "diagnosing",
      label: formatWorkflowStatus(displayValue || status || "diagnosing", "诊断中"),
      tone: status === "approved" ? "accent" : "info",
    };
  }

  return {
    key: "pending_diagnosis",
    label: "待诊断",
    tone: "warning",
  };
}

function buildAction(summary: DiagnosisSessionSummary | undefined, descriptor: SessionDescriptor): AlertDashboardAction {
  if (!summary?.session_id) {
    return {
      kind: "diagnose",
      label: "进入诊断",
      path: "/diagnosis",
    };
  }

  if (descriptor.key === "pending_remediation") {
    const normalizedStatus = normalizeStatusValue(summary.status);
    return {
      kind: "remediation",
      label: normalizedStatus === "approval_required" || normalizedStatus === "awaiting_approval" ? "审批修复" : "查看修复",
      path: `/remediation?sessionId=${encodeURIComponent(summary.session_id)}`,
    };
  }

  return {
    kind: "diagnosis",
    label: "查看诊断",
    path: `/diagnosis/${encodeURIComponent(summary.session_id)}`,
  };
}

function getSummaryTimestamp(summary?: DiagnosisSessionSummary, session?: DiagnosisSession) {
  return summary?.updated_at ?? session?.alert.starts_at ?? new Date(0).toISOString();
}

function isSameCalendarDay(left: string, right: Date) {
  const date = new Date(left);
  if (Number.isNaN(date.getTime())) {
    return false;
  }

  return (
    date.getFullYear() === right.getFullYear() &&
    date.getMonth() === right.getMonth() &&
    date.getDate() === right.getDate()
  );
}

function buildSearchText(item: AlertDashboardItem) {
  return [
    item.title,
    item.summary,
    item.rootEntity,
    item.impactScope,
    item.statusLabel,
    item.sessionId,
    item.analysisSummary,
    item.planSummary,
    ...item.alertNames,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function getAlertDashboardSearchText(item: AlertDashboardItem) {
  return buildSearchText(item);
}

function chooseBestSummary(candidates: DiagnosisSessionSummary[], details: Record<string, DiagnosisSession | undefined>) {
  return [...candidates].sort((left, right) => {
    const leftDescriptor = resolveSessionDescriptor(left, details[left.session_id]);
    const rightDescriptor = resolveSessionDescriptor(right, details[right.session_id]);
    const statusGap = statusRank[leftDescriptor.key] - statusRank[rightDescriptor.key];
    if (statusGap !== 0) {
      return statusGap;
    }

    return right.updated_at.localeCompare(left.updated_at);
  })[0];
}

export function buildAlertDashboardView(
  alerts: Alert[],
  clusters: AlertCluster[],
  sessionSummaries: DiagnosisSessionSummary[],
  sessionDetails: Record<string, DiagnosisSession | undefined>,
  now = new Date(),
): AlertDashboardView {
  const alertsByFingerprint = new Map<string, Alert[]>();
  alerts.forEach((alert) => {
    const bucket = alertsByFingerprint.get(alert.fingerprint) ?? [];
    bucket.push(alert);
    alertsByFingerprint.set(alert.fingerprint, bucket);
  });

  const groups: Array<{ id: string; cluster?: AlertCluster; alerts: Alert[]; fingerprints: string[] }> = [];
  const consumedFingerprints = new Set<string>();

  clusters.forEach((cluster) => {
    const groupAlerts = cluster.alerts.flatMap((fingerprint) => alertsByFingerprint.get(fingerprint) ?? []);
    if (groupAlerts.length === 0) {
      return;
    }

    groups.push({
      id: cluster.cluster_id,
      cluster,
      alerts: groupAlerts,
      fingerprints: unique(groupAlerts.map((alert) => alert.fingerprint)),
    });

    cluster.alerts.forEach((fingerprint) => consumedFingerprints.add(fingerprint));
  });

  alertsByFingerprint.forEach((groupAlerts, fingerprint) => {
    if (consumedFingerprints.has(fingerprint)) {
      return;
    }

    groups.push({
      id: fingerprint,
      alerts: groupAlerts,
      fingerprints: [fingerprint],
    });
  });

  const items = groups.map((group) => {
    const primaryAlert = pickPrimaryAlert(group.alerts) ?? group.alerts[0];
    const fingerprints = unique(group.fingerprints);
    const matchingSummaries = sessionSummaries.filter(
      (summary) => summary.fingerprint && fingerprints.includes(summary.fingerprint),
    );
    const matchedSummary = chooseBestSummary(matchingSummaries, sessionDetails);
    const matchedSession = matchedSummary ? sessionDetails[matchedSummary.session_id] : undefined;
    const descriptor = resolveSessionDescriptor(matchedSummary, matchedSession);
    const action = buildAction(matchedSummary, descriptor);
    const alertNames = unique(group.alerts.map((alert) => alert.alert_name));
    const latestStartsAt = getLatestStartsAt(group.alerts);
    const analysisSummary = matchedSession?.diagnosis_result?.root_cause ?? null;
    const planSummary = matchedSession?.diagnosis_result?.recommended_fix?.description ?? null;

    return {
      id: group.id,
      title: buildTitle(group.cluster, group.alerts),
      summary: buildSummary(group.cluster, group.alerts),
      severity: highestSeverity(group.alerts),
      statusKey: descriptor.key,
      statusLabel: descriptor.label,
      statusTone: descriptor.tone,
      statusTimestamp: getSummaryTimestamp(matchedSummary, matchedSession),
      sessionId: matchedSummary?.session_id,
      latestStartsAt,
      primaryFingerprint: primaryAlert?.fingerprint ?? fingerprints[0] ?? "unknown",
      rootEntity: buildRootEntity(group.alerts, matchedSession),
      impactScope: buildImpactScope(group.alerts, matchedSession),
      alertNames,
      analysisSummary,
      confidence: matchedSession?.diagnosis_result?.confidence ?? null,
      planSummary,
      hasSession: Boolean(matchedSummary?.session_id),
      isSessionDetailLoaded: Boolean(matchedSession),
      action,
    } satisfies AlertDashboardItem;
  });

  items.sort((left, right) => {
    const severityGap = severityRank[left.severity] - severityRank[right.severity];
    if (severityGap !== 0) {
      return severityGap;
    }

    const statusGap = statusRank[left.statusKey] - statusRank[right.statusKey];
    if (statusGap !== 0) {
      return statusGap;
    }

    return new Date(right.latestStartsAt).getTime() - new Date(left.latestStartsAt).getTime();
  });

  return {
    items,
    metrics: {
      totalItems: items.length,
      diagnosingCount: items.filter((item) => item.statusKey === "diagnosing").length,
      pendingActionCount: items.filter((item) => item.statusKey === "pending_remediation").length,
      resolvedTodayCount: items.filter((item) => item.statusKey === "resolved" && isSameCalendarDay(item.statusTimestamp, now)).length,
    },
  };
}



