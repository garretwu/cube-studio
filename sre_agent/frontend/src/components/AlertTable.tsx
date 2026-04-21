import { Table } from "antd";

import { StatusChip } from "./ui";
import type { Alert } from "../api/types";
import { formatAlertStatus, formatSeverity } from "../utils/display";
import { formatTimestamp } from "../utils/format";
import { buildAlertIncidentKey } from "../utils/alerts";

type AlertTableProps = {
  alerts: Alert[];
  onSelect?: (alert: Alert) => void;
};

function severityTone(severity: Alert["severity"]) {
  switch (severity) {
    case "critical":
      return "danger";
    case "warning":
      return "warning";
    default:
      return "info";
  }
}

function statusTone(status: Alert["status"]) {
  switch (status) {
    case "resolved":
      return "success";
    case "silenced":
      return "neutral";
    default:
      return "accent";
  }
}

function AlertTable({ alerts, onSelect }: AlertTableProps) {
  return (
    <Table
      className="app-table"
      dataSource={alerts}
      pagination={false}
      rowKey={(record) => `${record.fingerprint}-${record.starts_at}`}
      columns={[
        {
          title: "级别",
          dataIndex: "severity",
          width: 120,
          render: (severity: Alert["severity"]) => (
            <StatusChip tone={severityTone(severity)}>{formatSeverity(severity)}</StatusChip>
          ),
        },
        {
          title: "告警",
          dataIndex: "alert_name",
          render: (_value: string, alert: Alert) => (
            <div className="page-stack" style={{ gap: "6px" }}>
              <p className="data-list__title">{alert.alert_name}</p>
              <p className="data-list__copy">{alert.annotations.summary ?? "暂无摘要。"}</p>
              <p className="data-list__copy">{`事件主键: ${buildAlertIncidentKey(alert)}`}</p>
            </div>
          ),
        },
        {
          title: "实体",
          render: (_value, alert) => alert.annotations.entity ?? alert.labels.instance ?? alert.labels.node ?? "未知",
        },
        {
          title: "开始时间",
          dataIndex: "starts_at",
          width: 130,
          render: (value: string) => formatTimestamp(value),
        },
        {
          title: "状态",
          dataIndex: "status",
          width: 120,
          render: (status: Alert["status"]) => (
            <StatusChip tone={statusTone(status)}>{formatAlertStatus(status)}</StatusChip>
          ),
        },
      ]}
      onRow={(record) => ({
        onClick: () => onSelect?.(record),
      })}
    />
  );
}

export default AlertTable;
