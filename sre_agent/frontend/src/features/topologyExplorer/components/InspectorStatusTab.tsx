import type { TopologyObject } from "../../../api/types";
import { formatTopologyMetricValue, formatTopologyStatus } from "../formatters";

type InspectorStatusTabProps = {
  node: TopologyObject;
};

function getStatusRows(node: TopologyObject) {
  const shared = [
    { label: "运行状态", value: formatTopologyStatus(node.status) },
    { label: "最近心跳时间", value: formatTopologyMetricValue(node.metrics?.heartbeatAt ?? node.updatedAt) },
  ];

  if (node.type === "gpu") {
    return [
      ...shared,
      { label: "GPU 利用率", value: formatTopologyMetricValue(node.metrics?.utilization) },
      { label: "显存占用", value: formatTopologyMetricValue(node.metrics?.memoryUsage) },
      { label: "温度", value: `${formatTopologyMetricValue(node.metrics?.temperature)}°C` },
    ];
  }

  if (node.type === "node") {
    return [
      ...shared,
      { label: "CPU 利用率", value: formatTopologyMetricValue(node.metrics?.cpuUsage) },
      { label: "内存占用", value: formatTopologyMetricValue(node.metrics?.memoryUsage) },
      { label: "工作负载数", value: formatTopologyMetricValue(node.metrics?.podCount) },
    ];
  }

  if (node.type === "service") {
    return [
      ...shared,
      { label: "P95 延迟", value: `${formatTopologyMetricValue(node.metrics?.p95LatencyMs)} ms` },
      { label: "QPS", value: formatTopologyMetricValue(node.metrics?.qps) },
      { label: "错误率", value: formatTopologyMetricValue(node.metrics?.errorRate) },
    ];
  }

  if (node.type === "switch") {
    return [
      ...shared,
      { label: "丢包率", value: formatTopologyMetricValue(node.metrics?.packetLoss) },
      { label: "端口利用率", value: formatTopologyMetricValue(node.metrics?.portUtilization) },
      { label: "交换时延", value: `${formatTopologyMetricValue(node.metrics?.latencyUs)} us` },
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
