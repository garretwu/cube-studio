import type { AppIconName } from "../../components/ui";
import type { TopologyImpactLevel, TopologyObject, TopologyObjectStatus, TopologyRelation } from "../../api/types";

export function formatTopologyType(value: TopologyObject["type"]) {
  const labels: Record<TopologyObject["type"], string> = {
    cluster: "集群",
    gpu: "GPU",
    node: "节点",
    rack: "机柜",
    service: "服务",
    switch: "交换机",
  };

  return labels[value];
}

export function getTopologyTypeIconName(value: TopologyObject["type"]): AppIconName {
  const icons: Record<TopologyObject["type"], AppIconName> = {
    cluster: "clusterMesh",
    gpu: "gpuChip",
    node: "serverNode",
    rack: "serverRack",
    service: "servicePulse",
    switch: "networkSwitch",
  };

  return icons[value];
}

export function formatTopologyLayer(value: TopologyObject["layer"]) {
  const labels: Record<TopologyObject["layer"], string> = {
    compute: "计算层",
    network: "网络层",
    physical: "物理层",
    service: "服务层",
  };

  return labels[value];
}

export function formatTopologyStatus(value: TopologyObjectStatus) {
  const labels: Record<TopologyObjectStatus, string> = {
    abnormal: "异常",
    healthy: "正常",
    impacted: "受影响",
    maintenance: "维护中",
  };

  return labels[value];
}

export function formatImpactLevel(value: TopologyImpactLevel) {
  const labels: Record<TopologyImpactLevel, string> = {
    high: "高",
    low: "低",
    medium: "中",
  };

  return labels[value];
}

export function formatRelationType(value: TopologyRelation["relationType"]) {
  const labels: Record<TopologyRelation["relationType"], string> = {
    aggregated: "跨层聚合",
    connects_to: "连接",
    contains: "包含",
    depends_on: "依赖",
    runs_on: "运行于",
    uplink_to: "上联",
  };

  return labels[value];
}

export function getStatusTone(value: TopologyObjectStatus) {
  const tones: Record<TopologyObjectStatus, "success" | "danger" | "warning" | "info"> = {
    abnormal: "danger",
    healthy: "success",
    impacted: "warning",
    maintenance: "info",
  };

  return tones[value];
}

export function formatTopologyMetricValue(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") {
    return "暂无";
  }

  if (typeof value === "number") {
    if (value > 0 && value < 1) {
      return `${Math.round(value * 100)}%`;
    }

    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }

  return value;
}

export function formatTopologyAttributeValue(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "暂无";
  }

  if (typeof value === "number") {
    return formatTopologyMetricValue(value);
  }

  if (typeof value === "string") {
    return value;
  }

  if (Array.isArray(value)) {
    return value.join(" / ");
  }

  if (typeof value === "object") {
    return JSON.stringify(value);
  }

  return String(value);
}

export function getLocationLabel(node: TopologyObject) {
  return [node.region, node.zone, node.cluster, node.rack, node.slot].filter(Boolean).join(" / ");
}

export function getNodeMetricSummary(node: TopologyObject) {
  if (!node.metrics) {
    return "无运行指标";
  }

  if (node.type === "cluster") {
    return `可调度 ${formatTopologyMetricValue(node.metrics.schedulableNodes)} · 活跃 Pod ${formatTopologyMetricValue(node.metrics.activePods)}`;
  }

  if (node.type === "rack") {
    return `进风 ${formatTopologyMetricValue(node.metrics.inletTemp)}°C · 功耗 ${formatTopologyMetricValue(node.metrics.powerKw)} kW`;
  }

  if (node.type === "gpu") {
    return `利用率 ${formatTopologyMetricValue(node.metrics.utilization)} · 温度 ${formatTopologyMetricValue(node.metrics.temperature)}°C`;
  }

  if (node.type === "service") {
    return `P95 ${formatTopologyMetricValue(node.metrics.p95LatencyMs)} ms · QPS ${formatTopologyMetricValue(node.metrics.qps)}`;
  }

  if (node.type === "switch") {
    return `丢包 ${formatTopologyMetricValue(node.metrics.packetLoss)} · 端口 ${formatTopologyMetricValue(node.metrics.portUtilization)}`;
  }

  if (node.type === "node") {
    return `CPU ${formatTopologyMetricValue(node.metrics.cpuUsage)} · 内存 ${formatTopologyMetricValue(node.metrics.memoryUsage)}`;
  }

  return "查看对象摘要";
}
