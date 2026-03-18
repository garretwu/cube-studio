import { useEffect, useState } from "react";
import { Card, Descriptions, List, Space, Tabs, Tag, Typography } from "antd";

import { apiClient } from "../api/client";
import type { ConfigBaseline, IncidentRecord, LearnedPattern } from "../api/types";

function MemoryPage() {
  const [incidents, setIncidents] = useState<IncidentRecord[]>([]);
  const [patterns, setPatterns] = useState<LearnedPattern[]>([]);
  const [baseline, setBaseline] = useState<ConfigBaseline | null>(null);

  useEffect(() => {
    void Promise.all([apiClient.getMemoryIncidents(), apiClient.getMemoryPatterns(), apiClient.getMemoryBaseline()]).then(
      ([loadedIncidents, loadedPatterns, loadedBaseline]) => {
        setIncidents(loadedIncidents);
        setPatterns(loadedPatterns);
        setBaseline(loadedBaseline);
      },
    );
  }, []);

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Typography.Title level={2} style={{ margin: 0 }}>
          Memory Surface
        </Typography.Title>
      </Card>
      <Tabs
        items={[
          {
            key: "incidents",
            label: "Incidents",
            children: (
              <Card className="panel-card">
                <List
                  dataSource={incidents}
                  renderItem={(item) => (
                    <List.Item>
                      <List.Item.Meta
                        title={
                          <Space wrap>
                            <Typography.Text strong>{item.root_cause}</Typography.Text>
                            <Tag>{item.outcome}</Tag>
                          </Space>
                        }
                        description={`${item.timestamp} • ${item.alert.alert_name} • ${item.resolution_time_seconds}s`}
                      />
                    </List.Item>
                  )}
                />
              </Card>
            ),
          },
          {
            key: "patterns",
            label: "Patterns",
            children: (
              <Card className="panel-card">
                <List
                  dataSource={patterns}
                  renderItem={(item) => (
                    <List.Item>
                      <List.Item.Meta
                        title={
                          <Space wrap>
                            <Typography.Text strong>{item.root_cause}</Typography.Text>
                            <Tag color="green">{Math.round(item.confidence * 100)}%</Tag>
                          </Space>
                        }
                        description={`${item.occurrence_count} occurrences • ${item.symptom_signature.join(", ")}`}
                      />
                    </List.Item>
                  )}
                />
              </Card>
            ),
          },
          {
            key: "baseline",
            label: "Config Baseline",
            children: (
              <Card className="panel-card">
                <Descriptions column={1}>
                  <Descriptions.Item label="Version">{baseline?.version}</Descriptions.Item>
                  <Descriptions.Item label="Metric Baselines">
                    <pre>{JSON.stringify(baseline?.metric_baselines ?? {}, null, 2)}</pre>
                  </Descriptions.Item>
                  <Descriptions.Item label="Safety Thresholds">
                    <pre>{JSON.stringify(baseline?.safety_thresholds ?? {}, null, 2)}</pre>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
}

export default MemoryPage;
