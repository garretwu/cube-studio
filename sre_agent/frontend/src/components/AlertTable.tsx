import { Table, Tag } from "antd";

import type { Alert } from "../api/types";

type AlertTableProps = {
  alerts: Alert[];
  onSelect?: (alert: Alert) => void;
};

function AlertTable({ alerts, onSelect }: AlertTableProps) {
  return (
    <Table
      rowKey="fingerprint"
      columns={[
        {
          title: "Severity",
          dataIndex: "severity",
          render: (severity: Alert["severity"]) => (
            <Tag color={severity === "critical" ? "red" : severity === "warning" ? "orange" : "cyan"}>
              {severity}
            </Tag>
          ),
        },
        {
          title: "Alert",
          dataIndex: "alert_name",
        },
        {
          title: "Entity",
          render: (_, alert) => alert.annotations.entity ?? alert.labels.instance ?? alert.labels.node ?? "unknown",
        },
        {
          title: "Started",
          dataIndex: "starts_at",
        },
        {
          title: "Status",
          dataIndex: "status",
        },
      ]}
      dataSource={alerts}
      pagination={false}
      onRow={(record) => ({
        onClick: () => onSelect?.(record),
      })}
    />
  );
}

export default AlertTable;
