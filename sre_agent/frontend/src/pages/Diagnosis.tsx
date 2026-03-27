import { useEffect } from "react";
import { Card, List, Space, Tag, Typography } from "antd";
import { useSearchParams } from "react-router-dom";

import ThinkingTimeline from "../components/ThinkingTimeline";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";

function DiagnosisPage() {
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get("session_id") ?? "";
  const { session, fetchSession, connectionState, applyEvent, setConnectionState, setSessionId } = useDiagnosisStore();

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    setSessionId(sessionId);
    void fetchSession(sessionId);
  }, [fetchSession, sessionId, setSessionId]);

  const wsToken = import.meta.env.VITE_API_TOKEN ?? "";
  const wsUrl = `${window.location.origin.replace(/^http/, "ws")}/ws/thinking-trace/${session?.session_id ?? "pending"}?token=${encodeURIComponent(wsToken)}`;
  const ws = useWebSocket(
    wsUrl,
    applyEvent,
    {
      enabled: import.meta.env.VITE_WS_ENABLED === "true" && Boolean(session?.session_id) && Boolean(wsToken),
    },
  );

  useEffect(() => {
    setConnectionState(ws.state);
  }, [setConnectionState, ws.state]);

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Space direction="vertical" size="small">
          <Typography.Title level={2} style={{ margin: 0 }}>
            Diagnosis Playback
          </Typography.Title>
          <Space>
            <Tag color={connectionState === "open" ? "green" : "blue"}>WS {connectionState}</Tag>
            <Tag>{session?.status ?? "loading"}</Tag>
            <Tag color="gold">{session?.diagnosis_result?.triage_priority ?? "P?"}</Tag>
          </Space>
        </Space>
      </Card>

      <div className="split-panel">
        <div>
          <Card className="panel-card" title="Thinking Timeline">
            <ThinkingTimeline steps={session?.trace?.steps ?? []} />
          </Card>
        </div>
        <div>
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Card className="panel-card" title="Hypothesis Tree">
              <List
                dataSource={session?.diagnosis_result?.hypotheses ?? []}
                renderItem={(hypothesis) => (
                  <List.Item>
                    <List.Item.Meta
                      title={
                        <Space>
                          <Tag color={hypothesis.status === "confirmed" ? "green" : hypothesis.status === "eliminated" ? "default" : "blue"}>
                            {hypothesis.status}
                          </Tag>
                          {hypothesis.description}
                        </Space>
                      }
                      description={`confidence ${Math.round(hypothesis.confidence * 100)}%`}
                    />
                  </List.Item>
                )}
              />
            </Card>
            <Card className="panel-card" title="Conclusion">
              <Space direction="vertical">
                <Typography.Text strong>{session?.diagnosis_result?.root_cause}</Typography.Text>
                <Typography.Text>{session?.diagnosis_result?.impact_summary}</Typography.Text>
                <Tag color="magenta">{Math.round((session?.diagnosis_result?.confidence ?? 0) * 100)}% confidence</Tag>
              </Space>
            </Card>
          </Space>
        </div>
      </div>
    </div>
  );
}

export default DiagnosisPage;
