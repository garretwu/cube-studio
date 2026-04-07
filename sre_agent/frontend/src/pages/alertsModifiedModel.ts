import type { Alert, AlertCluster, AlertStatus, DiagnosisSession } from "../api/types";

type ChipTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

type RouteDecision = {
  label: "并入已有诊断" | "将创建诊断" | "观察中";
  tone: ChipTone;
  ctaLabel: string;
  path: string;
  note: string;
};

export type ConvergenceResult = {
  id: string;
  title: string;
  summary: string;
  severity: Alert["severity"];
  alertCount: number;
  fingerprintCount: number;
  duplicateFoldedCount: number;
  impactScope: string;
  primaryJudgment: string;
  route: RouteDecision;
  fingerprints: string[];
  alertNames: string[];
  primaryFingerprint: string;
};

export type AlertFlowEvent = {
  id: string;
  alertName: string;
  severity: Alert["severity"];
  entity: string;
  startsAt: string;
  status: AlertStatus;
  summary: string;
};

export type AlertFlowGroup = {
  fingerprint: string;
  alertName: string;
  severity: Alert["severity"];
  entity: string;
  latestStartsAt: string;
  eventCount: number;
  fingerprintRole: string;
  fingerprintTone: ChipTone;
  compressionLabel: string;
  compressionTone: ChipTone;
  convergenceTarget: string;
  routeLabel: RouteDecision["label"];
  routeTone: ChipTone;
  events: AlertFlowEvent[];
};

export type AlertConvergenceView = {
  results: ConvergenceResult[];
  flow: AlertFlowGroup[];
};

const severityRank: Record<Alert["severity"], number> = {
  critical: 0,
  warning: 1,
  info: 2,
};

const routeRank: Record<RouteDecision["label"], number> = {
  "并入已有诊断": 0,
  "将创建诊断": 1,
  "观察中": 2,
};

function unique(values: Array<string | null | undefined>) {
  return [...new Set(values.filter((value): value is string => Boolean(value)))];
}

function sortByStartsAtDesc(left: Alert, right: Alert) {
  return new Date(right.starts_at).getTime() - new Date(left.starts_at).getTime();
}

function sortBySeverityThenTime(left: Alert, right: Alert) {
  const severityGap = severityRank[left.severity] - severityRank[right.severity];
  if (severityGap !== 0) {
    return severityGap;
  }

  return new Date(left.starts_at).getTime() - new Date(right.starts_at).getTime();
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

function normalizeSentence(value: string) {
  return value.replace(/[。；;，,\s]+$/u, "");
}

function buildConvergenceTitle(cluster: AlertCluster | undefined, alerts: Alert[]) {
  if (cluster?.summary) {
    return normalizeSentence(cluster.summary);
  }

  const services = unique(alerts.map((alert) => alert.labels.service));
  if (services.length > 0) {
    return `${services[0]} 告警收敛结果`;
  }

  return alerts[0]?.alert_name ?? "未命名收敛结果";
}

function buildSummary(alerts: Alert[]) {
  if (alerts.length === 1) {
    return "当前只有单条原始事件命中该处理入口。";
  }

  const fingerprintCount = unique(alerts.map((alert) => alert.fingerprint)).length;
  return `系统已将 ${alerts.length} 条事件压缩为 ${fingerprintCount} 个 fingerprint 入口。`;
}

function buildImpactScope(alerts: Alert[]) {
  const services = unique(alerts.map((alert) => alert.labels.service));
  const entities = unique(alerts.map((alert) => getAlertEntity(alert)));
  const nodes = unique(alerts.map((alert) => alert.labels.node));
  const parts: string[] = [];

  if (services.length > 0) {
    parts.push(`服务 ${services.slice(0, 2).join("、")}`);
  }

  if (entities.length > 0) {
    parts.push(`实体 ${entities.slice(0, 3).join("、")}`);
  }

  if (nodes.length > 0) {
    parts.push(`节点 ${nodes.slice(0, 2).join("、")}`);
  }

  return parts.join(" · ") || "影响范围仍在持续观察";
}

function buildPrimaryJudgment(cluster: AlertCluster | undefined, alerts: Alert[]) {
  const haystack = alerts
    .flatMap((alert) => [alert.alert_name, alert.annotations.summary ?? "", getAlertEntity(alert)])
    .join(" ")
    .toLowerCase();

  if (
    (haystack.includes("latency") || haystack.includes("延迟") || haystack.includes("时延")) &&
    (haystack.includes("gpu") || haystack.includes("温度") || haystack.includes("热"))
  ) {
    return "时延抬升与 GPU 热压同步出现，系统优先按同一资源争用面推进诊断。";
  }

  if (
    (haystack.includes("packet loss") || haystack.includes("丢包") || haystack.includes("rdma")) &&
    (haystack.includes("交换机") || haystack.includes("网络") || haystack.includes("roce"))
  ) {
    return "多条信号都落在网络层同一影响面，建议按链路拥塞或配置漂移统一排查。";
  }

  if (cluster?.summary) {
    return `当前主判断：${normalizeSentence(cluster.summary)}。`;
  }

  return "系统判断这些信号足以形成单一处理入口，后续只需要围绕收敛结果推进。";
}

function buildRouteDecision(alerts: Alert[], activeSession?: DiagnosisSession) {
  const focusFingerprint = encodeURIComponent(pickPrimaryFingerprint(alerts));
  if (activeSession && alerts.some((alert) => alert.fingerprint === activeSession.alert.fingerprint)) {
    return {
      label: "并入已有诊断" as const,
      tone: "accent" as const,
      ctaLabel: "查看诊断",
      path: `/diagnosis/${activeSession.session_id}`,
      note: `已命中诊断 ${activeSession.session_id}`,
    };
  }

  const allQuiet = alerts.every((alert) => alert.status !== "firing");
  if (allQuiet) {
    return {
      label: "观察中" as const,
      tone: "neutral" as const,
      ctaLabel: "回看原始告警",
      path: `/alerts-modified?q=${focusFingerprint}`,
      note: "当前不新建诊断会话",
    };
  }

  if (alerts.length > 1 || alerts.some((alert) => alert.severity === "critical")) {
    return {
      label: "将创建诊断" as const,
      tone: "danger" as const,
      ctaLabel: "进入诊断",
      path: "/diagnosis",
      note: "系统将按收敛结果统一创建诊断",
    };
  }

  return {
    label: "观察中" as const,
    tone: "warning" as const,
    ctaLabel: "继续观察",
    path: `/alerts-modified?q=${focusFingerprint}`,
    note: "先保留在观察队列",
  };
}

function pickPrimaryFingerprint(alerts: Alert[]) {
  const ranked = [...alerts].sort(sortBySeverityThenTime);
  return ranked[0]?.fingerprint ?? alerts[0]?.fingerprint ?? "unknown";
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

function buildResult(cluster: AlertCluster | undefined, alerts: Alert[], activeSession?: DiagnosisSession): ConvergenceResult {
  const route = buildRouteDecision(alerts, activeSession);
  const fingerprints = unique(alerts.map((alert) => alert.fingerprint));

  return {
    id: cluster?.cluster_id ?? alerts[0]?.fingerprint ?? "orphan-result",
    title: buildConvergenceTitle(cluster, alerts),
    summary: buildSummary(alerts),
    severity: highestSeverity(alerts),
    alertCount: alerts.length,
    fingerprintCount: fingerprints.length,
    duplicateFoldedCount: Math.max(alerts.length - fingerprints.length, 0),
    impactScope: buildImpactScope(alerts),
    primaryJudgment: buildPrimaryJudgment(cluster, alerts),
    route,
    fingerprints,
    alertNames: unique(alerts.map((alert) => alert.alert_name)),
    primaryFingerprint: pickPrimaryFingerprint(alerts),
  };
}

function buildFlowGroup(fingerprint: string, alerts: Alert[], result: ConvergenceResult): AlertFlowGroup {
  const events = [...alerts]
    .sort(sortByStartsAtDesc)
    .map((alert, index) => ({
      id: `${fingerprint}-${index}-${alert.starts_at}`,
      alertName: alert.alert_name,
      severity: alert.severity,
      entity: getAlertEntity(alert),
      startsAt: alert.starts_at,
      status: alert.status,
      summary: alert.annotations.summary ?? "无额外摘要",
    }));
  const latest = events[0];
  const isPrimary = result.primaryFingerprint === fingerprint;

  return {
    fingerprint,
    alertName: latest?.alertName ?? alerts[0]?.alert_name ?? "未命名告警",
    severity: highestSeverity(alerts),
    entity: latest?.entity ?? getAlertEntity(alerts[0]),
    latestStartsAt: latest?.startsAt ?? alerts[0]?.starts_at ?? new Date(0).toISOString(),
    eventCount: alerts.length,
    fingerprintRole: isPrimary ? "主指纹" : "参与收敛",
    fingerprintTone: isPrimary ? "accent" : "neutral",
    compressionLabel: alerts.length > 1 ? `${alerts.length} 次事件` : "1 次事件",
    compressionTone: alerts.length > 1 ? "accent" : "success",
    convergenceTarget: result.title,
    routeLabel: result.route.label,
    routeTone: result.route.tone,
    events,
  };
}

export function buildAlertConvergenceView(
  alerts: Alert[],
  clusters: AlertCluster[],
  activeSession?: DiagnosisSession,
): AlertConvergenceView {
  const alertsByFingerprint = new Map<string, Alert[]>();
  alerts.forEach((alert) => {
    const bucket = alertsByFingerprint.get(alert.fingerprint) ?? [];
    bucket.push(alert);
    alertsByFingerprint.set(alert.fingerprint, bucket);
  });

  const results: ConvergenceResult[] = [];
  const resultByFingerprint = new Map<string, ConvergenceResult>();
  const consumedFingerprints = new Set<string>();

  clusters.forEach((cluster) => {
    const clusterAlerts = cluster.alerts.flatMap((fingerprint) => alertsByFingerprint.get(fingerprint) ?? []);
    if (clusterAlerts.length === 0) {
      return;
    }

    const result = buildResult(cluster, clusterAlerts, activeSession);
    results.push(result);

    cluster.alerts.forEach((fingerprint) => {
      if (!alertsByFingerprint.has(fingerprint)) {
        return;
      }

      consumedFingerprints.add(fingerprint);
      resultByFingerprint.set(fingerprint, result);
    });
  });

  alertsByFingerprint.forEach((groupAlerts, fingerprint) => {
    if (consumedFingerprints.has(fingerprint)) {
      return;
    }

    const result = buildResult(undefined, groupAlerts, activeSession);
    results.push(result);
    resultByFingerprint.set(fingerprint, result);
  });

  const flow = [...alertsByFingerprint.entries()]
    .map(([fingerprint, groupAlerts]) => {
      const result = resultByFingerprint.get(fingerprint);
      if (!result) {
        return null;
      }

      return buildFlowGroup(fingerprint, groupAlerts, result);
    })
    .filter((group): group is AlertFlowGroup => Boolean(group))
    .sort((left, right) => {
      const timeGap = new Date(right.latestStartsAt).getTime() - new Date(left.latestStartsAt).getTime();
      if (timeGap !== 0) {
        return timeGap;
      }

      return severityRank[left.severity] - severityRank[right.severity];
    });

  results.sort((left, right) => {
    const routeGap = routeRank[left.route.label] - routeRank[right.route.label];
    if (routeGap !== 0) {
      return routeGap;
    }

    const severityGap = severityRank[left.severity] - severityRank[right.severity];
    if (severityGap !== 0) {
      return severityGap;
    }

    return right.alertCount - left.alertCount;
  });

  return { results, flow };
}

