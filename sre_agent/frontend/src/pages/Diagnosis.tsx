import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

import ThinkingTimeline from "../components/ThinkingTimeline";
import { SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";
import { formatLayer, formatWorkflowStatus } from "../utils/display";
import { formatPercent } from "../utils/format";

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
  const bootstrapLastEventId = Math.max(0, session?.trace?.steps?.length ?? 0);
  const wsUrl = `${window.location.origin.replace(/^http/, "ws")}/ws/thinking-trace/${session?.session_id ?? "pending"}?token=${encodeURIComponent(wsToken)}&last_event_id=${bootstrapLastEventId}`;
  const ws = useWebSocket(wsUrl, applyEvent, {
    enabled: import.meta.env.VITE_WS_ENABLED === "true" && Boolean(session?.session_id) && Boolean(wsToken),
  });

  useEffect(() => {
    setConnectionState(ws.state);
  }, [setConnectionState, ws.state]);

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          eyebrow="推理回放"
          title="诊断过程回放"
          description="回放智能体的诊断推理轨迹，检查候选假设与证据，再决定是否进入修复流程。"
        />
        <SurfaceCard bodyClassName="page-stack" variant="hero">
          <div className="status-row">
            <StatusChip tone={connectionState === "open" ? "success" : "info"}>WS {formatWorkflowStatus(connectionState)}</StatusChip>
            <StatusChip tone="neutral">{formatWorkflowStatus(session?.status, "加载中")}</StatusChip>
            <StatusChip tone="warning">{session?.diagnosis_result?.triage_priority ?? "P?"}</StatusChip>
          </div>
        </SurfaceCard>
      </div>

      <div className="page-two-col">
        <SurfaceCard title="推理时间线" description="本次诊断会话中的推理步骤、工具调用与关键观测按时间顺序展开。">
          <ThinkingTimeline steps={session?.trace?.steps ?? []} />
        </SurfaceCard>

        <div className="page-stack">
          <SurfaceCard title="假设树" description="当前正在比较的候选解释及其置信度态势。">
            <div className="mini-card-list">
              {(session?.diagnosis_result?.hypotheses ?? []).map((hypothesis) => (
                <div key={hypothesis.description} className="mini-card">
                  <div className="status-row">
                    <StatusChip
                      tone={
                        hypothesis.status === "confirmed"
                          ? "success"
                          : hypothesis.status === "eliminated"
                            ? "neutral"
                            : "info"
                      }
                    >
                      {formatWorkflowStatus(hypothesis.status)}
                    </StatusChip>
                    <StatusChip tone="accent">{formatPercent(hypothesis.confidence)}</StatusChip>
                  </div>
                  <p className="mini-card__title">{hypothesis.description}</p>
                </div>
              ))}
            </div>
          </SurfaceCard>

          <SurfaceCard title="结论摘要" description="诊断流程综合输出的根因、影响范围与置信度摘要。">
            <div className="page-stack">
              <div className="status-row">
                <StatusChip tone="accent">{formatPercent(session?.diagnosis_result?.confidence ?? 0)}</StatusChip>
                <StatusChip tone="neutral">{formatLayer(session?.diagnosis_result?.root_cause_layer) ?? "未知层级"}</StatusChip>
              </div>
              <div className="mini-card">
                <p className="mini-card__title">{session?.diagnosis_result?.root_cause ?? "等待诊断结论"}</p>
                <p className="mini-card__copy">{session?.diagnosis_result?.impact_summary ?? "当前暂无影响摘要。"}</p>
              </div>
            </div>
          </SurfaceCard>
        </div>
      </div>
    </div>
  );
}

export default DiagnosisPage;
