import type { AppIconName } from "../../components/ui";
import type { TopologyImpactLevel, TopologyObject, TopologyObjectStatus, TopologyRelation } from "../../api/types";
import topologyClusterIcon from "../../assets/topology-icons/topology-cluster.svg";
import topologyGpuIcon from "../../assets/topology-icons/topology-gpu.svg";
import topologyRackIcon from "../../assets/topology-icons/topology-rack.svg";
import topologyServerIcon from "../../assets/topology-icons/topology-server.svg";
import topologyServiceIcon from "../../assets/topology-icons/topology-service.svg";
import topologySwitchIcon from "../../assets/topology-icons/topology-switch.svg";

export function formatTopologyType(value: TopologyObject["type"]) {
  const labels: Record<TopologyObject["type"], string> = {
    cluster: "\u96c6\u7fa4",
    gpu: "GPU",
    node: "\u8282\u70b9",
    rack: "\u673a\u67dc",
    service: "\u670d\u52a1",
    switch: "\u4ea4\u6362\u673a",
    port: "\u7aef\u53e3",
    bmc: "BMC",
  };

  return labels[value];
}

export function getTopologyTypeIconAsset(value: TopologyObject["type"]) {
  const icons: Record<TopologyObject["type"], string> = {
    cluster: topologyClusterIcon,
    gpu: topologyGpuIcon,
    node: topologyServerIcon,
    rack: topologyRackIcon,
    service: topologyServiceIcon,
    switch: topologySwitchIcon,
    port: topologySwitchIcon,
    bmc: topologyServerIcon,
  };

  return icons[value];
}

export function getTopologyTypeIconName(value: TopologyObject["type"]): AppIconName {
  const icons: Record<TopologyObject["type"], AppIconName> = {
    cluster: "clusterMesh",
    gpu: "gpuChip",
    node: "serverNode",
    rack: "serverRack",
    service: "servicePulse",
    switch: "networkSwitch",
    port: "networkSwitch",
    bmc: "serverNode",
  };

  return icons[value];
}

export function formatTopologyLayer(value: TopologyObject["layer"]) {
  const labels: Record<TopologyObject["layer"], string> = {
    compute: "\u8ba1\u7b97\u5c42",
    network: "\u7f51\u7edc\u5c42",
    physical: "\u7269\u7406\u5c42",
    service: "\u670d\u52a1\u5c42",
  };

  return labels[value];
}

export function formatTopologyStatus(value: TopologyObjectStatus) {
  const labels: Record<TopologyObjectStatus, string> = {
    abnormal: "\u5f02\u5e38",
    healthy: "\u6b63\u5e38",
    impacted: "\u53d7\u5f71\u54cd",
    maintenance: "\u7ef4\u62a4\u4e2d",
  };

  return labels[value];
}

export function formatImpactLevel(value: TopologyImpactLevel) {
  const labels: Record<TopologyImpactLevel, string> = {
    high: "\u9ad8",
    low: "\u4f4e",
    medium: "\u4e2d",
  };

  return labels[value];
}

export function formatRelationType(value: TopologyRelation["relationType"]) {
  const labels: Record<TopologyRelation["relationType"], string> = {
    aggregated: "\u8de8\u5c42\u805a\u5408",
    connects_to: "\u8fde\u63a5",
    contains: "\u5305\u542b",
    depends_on: "\u4f9d\u8d56",
    runs_on: "\u8fd0\u884c\u4e8e",
    uplink_to: "\u4e0a\u8054",
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
    return "\u6682\u65e0";
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
    return "\u6682\u65e0";
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
  if (node.type === "bmc") {
    return `IP ${formatTopologyAttributeValue(node.attributes.ip)} / Source ${formatTopologyAttributeValue(node.attributes.source)}`;
  }

  if (!node.metrics) {
    return "\u65e0\u8fd0\u884c\u6307\u6807";
  }

  if (node.type === "cluster") {
    return `\u53ef\u8c03\u5ea6 ${formatTopologyMetricValue(node.metrics.schedulableNodes)} / \u6d3b\u8dc3 Pod ${formatTopologyMetricValue(node.metrics.activePods)}`;
  }

  if (node.type === "rack") {
    return `\u8fdb\u98ce ${formatTopologyMetricValue(node.metrics.inletTemp)} C / \u529f\u8017 ${formatTopologyMetricValue(node.metrics.powerKw)} kW`;
  }

  if (node.type === "gpu") {
    return `\u5229\u7528\u7387 ${formatTopologyMetricValue(node.metrics.utilization)} / \u6e29\u5ea6 ${formatTopologyMetricValue(node.metrics.temperature)} C`;
  }

  if (node.type === "service") {
    return `P95 ${formatTopologyMetricValue(node.metrics.p95LatencyMs)} ms / QPS ${formatTopologyMetricValue(node.metrics.qps)}`;
  }

  if (node.type === "switch") {
    return `\u4e22\u5305 ${formatTopologyMetricValue(node.metrics.packetLoss)} / \u7aef\u53e3 ${formatTopologyMetricValue(node.metrics.portUtilization)}`;
  }

  if (node.type === "port") {
    return `Speed ${formatTopologyAttributeValue(node.metrics?.speedGbps ?? node.attributes.speed_gbps)} Gbps / Status ${formatTopologyAttributeValue(node.attributes.status)}`;
  }

  if (node.type === "node") {
    return `CPU ${formatTopologyMetricValue(node.metrics.cpuUsage)} / \u5185\u5b58 ${formatTopologyMetricValue(node.metrics.memoryUsage)}`;
  }

  return "\u67e5\u770b\u5bf9\u8c61\u6458\u8981";
}