import { useEffect, useMemo } from "react";
import { Card, Col, Input, Row, Select, Space, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";

import AlertTable from "../components/AlertTable";
import { apiClient } from "../api/client";
import { useAlertStore } from "../store/alertStore";

function AlertsPage() {
  const navigate = useNavigate();
  const { alerts, clusters, severityFilter, fetchAlerts, setSeverityFilter } = useAlertStore();

  useEffect(() => {
    void fetchAlerts();
  }, [fetchAlerts]);

  const filtered = useMemo(
    () => alerts.filter((alert) => severityFilter === "all" || alert.severity === severityFilter),
    [alerts, severityFilter],
  );

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Typography.Title level={2} style={{ margin: 0 }}>
            Alert Correlation Board
          </Typography.Title>
          <Row gutter={[16, 16]} align="middle">
            <Col xs={24} md={8}>
              <Select
                value={severityFilter}
                style={{ width: "100%" }}
                onChange={setSeverityFilter}
                options={[
                  { value: "all", label: "All severities" },
                  { value: "critical", label: "Critical" },
                  { value: "warning", label: "Warning" },
                  { value: "info", label: "Info" },
                ]}
              />
            </Col>
            <Col xs={24} md={8}>
              <Input.Search placeholder="Filter by entity, alert name, or service" />
            </Col>
            <Col xs={24} md={8}>
              <Tag color="red" style={{ padding: "10px 12px" }}>
                {filtered.length} active candidates
              </Tag>
            </Col>
          </Row>
        </Space>
      </Card>

      <Row gutter={[20, 20]}>
        <Col xs={24} xl={16}>
          <Card className="panel-card" title="Live Alert Stream">
            <AlertTable
              alerts={filtered}
              onSelect={async (alert) => {
                const loop = await apiClient.handleAlert(alert);
                navigate(`/diagnosis?session_id=${encodeURIComponent(loop.session_id)}`);
              }}
            />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card className="panel-card" title="Correlation Groups">
            <Space direction="vertical" style={{ width: "100%" }}>
              {clusters.map((cluster) => (
                <Card key={cluster.cluster_id} size="small">
                  <Space direction="vertical">
                    <Tag color={cluster.severity === "critical" ? "red" : "orange"}>{cluster.severity}</Tag>
                    <Typography.Text strong>{cluster.summary}</Typography.Text>
                    <Typography.Text type="secondary">{cluster.alerts.length} alerts in the same window</Typography.Text>
                  </Space>
                </Card>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
}

export default AlertsPage;
