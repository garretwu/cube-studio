<<<<<<< HEAD
import { useCallback, useEffect, useMemo } from "react";
import { Card, Col, Input, Row, Select, Space, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";

import AlertTable from "../components/AlertTable";
import { apiClient } from "../api/client";
import type { WSEvent } from "../api/types";
import { useWebSocket } from "../hooks/useWebSocket";
=======
import { useEffect, useMemo, useState } from "react";
import { Select } from "antd";
import { useNavigate } from "react-router-dom";

import AlertTable from "../components/AlertTable";
import { AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)
import { useAlertStore } from "../store/alertStore";
import { formatSeverity } from "../utils/display";

function AlertsPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const { alerts, clusters, severityFilter, fetchAlerts, setSeverityFilter } = useAlertStore();

  useEffect(() => {
    void fetchAlerts();
  }, [fetchAlerts]);

  const wsToken = import.meta.env.VITE_API_TOKEN ?? "";
  const wsEnabled = import.meta.env.VITE_WS_ENABLED === "true";
  const alertsWsUrl = `${window.location.origin.replace(/^http/, "ws")}/ws/alerts?token=${encodeURIComponent(wsToken)}`;
  const handleAlertsEvent = useCallback(
    (event: WSEvent) => {
      if (event.type === "alert") {
        void fetchAlerts();
      }
    },
    [fetchAlerts],
  );
  const ws = useWebSocket(alertsWsUrl, handleAlertsEvent, {
    enabled: wsEnabled && Boolean(wsToken),
  });

  const filtered = useMemo(
    () =>
      alerts.filter((alert) => {
        const matchesSeverity = severityFilter === "all" || alert.severity === severityFilter;
        const haystack = [
          alert.alert_name,
          alert.annotations.summary,
          alert.annotations.entity,
          alert.labels.instance,
          alert.labels.node,
          alert.labels.service,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();

        const matchesQuery = !query.trim() || haystack.includes(query.trim().toLowerCase());
        return matchesSeverity && matchesQuery;
      }),
    [alerts, query, severityFilter],
  );

  return (
    <div className="page-grid">
<<<<<<< HEAD
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
              <Tag color={ws.state === "open" ? "green" : "blue"} style={{ marginInlineStart: 8, padding: "10px 12px" }}>
                WS {ws.state}
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
=======
      <div className="page-intro">
        <SectionHeader
          description="按级别、实体与服务上下文筛选告警候选，在进入诊断前先完成快速归并与聚焦。"
          eyebrow="告警关联"
          title="告警关联看板"
        />
        <SurfaceCard bodyClassName="page-stack" variant="hero">
          <div className="input-row">
            <Select
              className="app-select"
              onChange={setSeverityFilter}
              options={[
                { value: "all", label: formatSeverity("all") },
                { value: "critical", label: formatSeverity("critical") },
                { value: "warning", label: formatSeverity("warning") },
                { value: "info", label: formatSeverity("info") },
              ]}
              value={severityFilter}
            />
            <AppInput
              onChange={setQuery}
              placeholder="按实体、告警名称或服务筛选"
              prefix={<AppIcon name="search" size={16} />}
              value={query}
            />
            <StatusChip tone="danger">{filtered.length} 条候选告警</StatusChip>
          </div>
        </SurfaceCard>
      </div>

      <div className="page-two-col">
        <SurfaceCard description="已准备好进入关联分析和诊断流转的实时告警。" title="实时告警流">
          <AlertTable alerts={filtered} onSelect={() => navigate("/diagnosis")} />
        </SurfaceCard>

        <SurfaceCard description="已归并为潜在事件叙事的告警分组摘要。" title="关联分组">
          <div className="mini-card-list">
            {clusters.map((cluster) => (
              <div key={cluster.cluster_id} className="mini-card">
                <div className="status-row">
                  <StatusChip tone={cluster.severity === "critical" ? "danger" : "warning"}>
                    {formatSeverity(cluster.severity)}
                  </StatusChip>
                  <StatusChip tone="neutral">{cluster.alerts.length} 条告警</StatusChip>
                </div>
                <p className="mini-card__title">{cluster.summary}</p>
                <p className="mini-card__copy">分组编号：{cluster.cluster_id}</p>
              </div>
            ))}
          </div>
        </SurfaceCard>
      </div>
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)
    </div>
  );
}

export default AlertsPage;
