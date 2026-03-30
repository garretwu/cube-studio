import { Select } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { buildBackendWsUrl } from "../api/ws";
import type { SessionSummary } from "../api/types";
import ThinkingTimeline from "../components/ThinkingTimeline";
import { SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";
import { formatLayer, formatWorkflowStatus } from "../utils/display";
import { formatPercent } from "../utils/format";

function DiagnosisPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionId = (searchParams.get("session_id") ?? "").trim();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [sessionsError, setSessionsError] = useState("");
  const bootstrappedRef = useRef(false);
  const { session, fetchSession, connectionState, applyEvent, setConnectionState, setSessionId } = useDiagnosisStore();

  const loadSessions = useCallback(
    async (requested: string) => {
      setSessionsLoading(true);
      setSessionsError("");
      try {
        const records = await apiClient.getSessions(50);
        setSessions(records);
        const fallback = records[0]?.session_id ?? "";
        const selected = records.some((item) => item.session_id === requested) ? requested : fallback;
        if (selected && selected !== requested) {
          setSearchParams({ session_id: selected }, { replace: true });
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to load diagnosis sessions";
        if (/not found|session_id is required/i.test(message)) {
          setSessionsError("");
        } else {
          setSessionsError(message);
        }
        setSessions([]);
      } finally {
        setSessionsLoading(false);
        setSessionsLoaded(true);
      }
    },
    [setSearchParams],
  );

  useEffect(() => {
    if (bootstrappedRef.current) {
      return;
    }
    bootstrappedRef.current = true;
    void loadSessions(sessionId);
  }, [loadSessions, sessionId]);

  useEffect(() => {
    if (!sessionsLoaded || !sessionId) {
      return;
    }
    setSessionId(sessionId);
    void fetchSession(sessionId).catch((error: unknown) => {
      const message = error instanceof Error ? error.message : "Failed to load diagnosis session";
      if (/not found|session_id is required/i.test(message)) {
        setSessionsError("");
      } else {
        setSessionsError(message);
      }
    });
  }, [fetchSession, sessionId, sessionsLoaded, setSessionId]);

  const wsToken = import.meta.env.VITE_API_TOKEN ?? "";
  const bootstrapLastEventId = Math.max(0, session?.trace?.steps?.length ?? 0);
  const wsUrl = buildBackendWsUrl(`/ws/thinking-trace/${session?.session_id ?? "pending"}`, {
    token: wsToken,
    last_event_id: bootstrapLastEventId,
  });
  const ws = useWebSocket(wsUrl, applyEvent, {
    enabled: import.meta.env.VITE_WS_ENABLED === "true" && Boolean(session?.session_id) && Boolean(wsToken),
  });

  useEffect(() => {
    setConnectionState(ws.state);
  }, [setConnectionState, ws.state]);

  const sessionOptions = useMemo(
    () =>
      sessions.map((item) => ({
        value: item.session_id,
        label: `${item.session_id} | ${item.alert_name} | ${item.status}`,
      })),
    [sessions],
  );
  const wsStatusTone = session?.session_id ? (connectionState === "open" ? "success" : "info") : "neutral";
  const wsStatusText = session?.session_id ? `WS ${formatWorkflowStatus(connectionState)}` : "WS 等待会话";

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          eyebrow="推理回放"
          title="诊断过程回放"
          description="回放智能体诊断链路，支持在诊断页切换会话查看不同 session 的过程与结论。"
        />
        <SurfaceCard bodyClassName="page-stack" variant="hero">
          <div className="input-row">
            <Select
              className="app-select"
              value={sessionId || undefined}
              options={sessionOptions}
              loading={sessionsLoading}
              placeholder="选择诊断会话"
              onChange={(value) => {
                setSearchParams({ session_id: value });
              }}
            />
            <StatusChip tone={wsStatusTone}>{wsStatusText}</StatusChip>
            <StatusChip tone="neutral">{formatWorkflowStatus(session?.status, "加载中")}</StatusChip>
            <StatusChip tone="warning">{session?.diagnosis_result?.triage_priority ?? "P?"}</StatusChip>
            <StatusChip tone="neutral">会话 {sessions.length}</StatusChip>
          </div>
        </SurfaceCard>
      </div>

      {sessionsError ? (
        <SurfaceCard title="诊断请求失败" description="会话列表或会话详情加载失败。">
          <div className="mini-card">
            <p className="mini-card__copy">{sessionsError}</p>
          </div>
        </SurfaceCard>
      ) : null}

      {sessionsLoaded && sessions.length === 0 ? (
        <SurfaceCard title="暂无诊断会话" description="诊断会话由告警页手工触发。">
          <div className="mini-card">
            <p className="mini-card__copy">
              请先到告警页选择一条 firing 告警发起诊断，再回到本页查看会话推理轨迹。
            </p>
          </div>
        </SurfaceCard>
      ) : null}

      <div className="page-two-col">
        <SurfaceCard title="推理时间线" description="按时间顺序展开诊断步骤、工具调用与关键观测。">
          <ThinkingTimeline steps={session?.trace?.steps ?? []} />
        </SurfaceCard>

        <div className="page-stack">
          <SurfaceCard title="假设树" description="当前候选根因与置信度变化。">
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

          <SurfaceCard title="结论摘要" description="根因、影响范围与置信度摘要。">
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
