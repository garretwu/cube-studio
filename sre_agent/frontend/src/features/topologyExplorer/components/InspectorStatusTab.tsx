import type { TopologyObject } from "../../../api/types";
import { formatTopologyAttributeValue, formatTopologyMetricValue, formatTopologyStatus } from "../formatters";

type InspectorStatusTabProps = {
  node: TopologyObject;
};

function getStatusRows(node: TopologyObject) {
  const shared = [
    { label: "\u8fd0\u884c\u72b6\u6001", value: formatTopologyStatus(node.status) },
    { label: "\u6700\u8fd1\u5fc3\u8df3\u65f6\u95f4", value: formatTopologyMetricValue(node.metrics?.heartbeatAt ?? node.updatedAt) },
  ];

  if (node.type === "gpu") {
    return [
      ...shared,
      { label: "GPU \u5229\u7528\u7387", value: formatTopologyMetricValue(node.metrics?.utilization) },
      { label: "\u663e\u5b58\u5360\u7528", value: formatTopologyMetricValue(node.metrics?.memoryUsage) },
      { label: "\u6e29\u5ea6", value: `${formatTopologyMetricValue(node.metrics?.temperature)} C` },
    ];
  }

  if (node.type === "node") {
    return [
      ...shared,
      { label: "CPU \u5229\u7528\u7387", value: formatTopologyMetricValue(node.metrics?.cpuUsage) },
      { label: "\u5185\u5b58\u5360\u7528", value: formatTopologyMetricValue(node.metrics?.memoryUsage) },
      { label: "\u5de5\u4f5c\u8d1f\u8f7d\u6570", value: formatTopologyMetricValue(node.metrics?.podCount) },
    ];
  }

  if (node.type === "service") {
    return [
      ...shared,
      { label: "P95 \u5ef6\u8fdf", value: `${formatTopologyMetricValue(node.metrics?.p95LatencyMs)} ms` },
      { label: "QPS", value: formatTopologyMetricValue(node.metrics?.qps) },
      { label: "\u9519\u8bef\u7387", value: formatTopologyMetricValue(node.metrics?.errorRate) },
    ];
  }

  if (node.type === "switch") {
    return [
      ...shared,
      { label: "\u4e22\u5305\u7387", value: formatTopologyMetricValue(node.metrics?.packetLoss) },
      { label: "\u7aef\u53e3\u5229\u7528\u7387", value: formatTopologyMetricValue(node.metrics?.portUtilization) },
      { label: "\u4ea4\u6362\u65f6\u5ef6", value: `${formatTopologyMetricValue(node.metrics?.latencyUs)} us` },
    ];
  }

  if (node.type === "bmc") {
    return [
      ...shared,
      { label: "BMC IP", value: formatTopologyAttributeValue(node.attributes.ip) },
      { label: "Source", value: formatTopologyAttributeValue(node.attributes.source) },
    ];
  }

  if (node.type === "port") {
    return [
      ...shared,
      { label: "Port speed", value: `${formatTopologyAttributeValue(node.metrics?.speedGbps ?? node.attributes.speed_gbps)} Gbps` },
      { label: "Port status", value: formatTopologyAttributeValue(node.attributes.status) },
      { label: "Mapping source", value: formatTopologyAttributeValue(node.attributes.mapping_source) },
    ];
  }

  return shared;
}

function InspectorStatusTab({ node }: InspectorStatusTabProps) {
  const rows = getStatusRows(node);

  return (
    <div className="topology-modified-inspector-tab">
      <div className="topology-modified-data-list">
        {rows.map((row) => (
          <div key={row.label} className="topology-modified-data-list__row">
            <span className="topology-modified-detail-grid__label">{row.label}</span>
            <span className="topology-modified-detail-grid__value">{row.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default InspectorStatusTab;