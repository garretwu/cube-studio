import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { apiClient } from "../api/client";
import type { Alert, AlertCluster, DiagnosisSession, DiagnosisSessionSummary } from "../api/types";
import { useAlertStore } from "../store/alertStore";
import AlertsModifiedPage from "./AlertsModified";

const alerts: Alert[] = [
  {
    alert_name: "VLLM 延迟过高",
    severity: "critical",
    labels: { service: "vllm", instance: "vllm-0", aidc: "aidc-001" },
    annotations: { summary: "p95 延迟超过 600ms", entity: "svc-vllm" },
    starts_at: "2026-03-18T12:00:00Z",
    fingerprint: "fp-001",
    status: "firing",
    source: "alertmanager",
  },
  {
    alert_name: "GPU 温度偏高",
    severity: "warning",
    labels: { node: "node-gpu-01", gpu: "gpu-01", aidc: "aidc-001" },
    annotations: { summary: "GPU 温度持续高于目标阈值", entity: "gpu-01" },
    starts_at: "2026-03-18T11:57:00Z",
    fingerprint: "fp-002",
    status: "firing",
    source: "prometheus",
  },
  {
    alert_name: "NCCLTimeout",
    severity: "critical",
    labels: { node: "sw-200g", service: "trainer-gateway", aidc: "aidc-001" },
    annotations: { summary: "交换机端口出现持续拥塞", entity: "sw-200g:HGE1/0/1" },
    starts_at: "2026-03-18T12:03:00Z",
    fingerprint: "fp-003",
    status: "firing",
    source: "alertmanager",
  },
];

const clusters: AlertCluster[] = [
  {
    cluster_id: "cluster-01",
    summary: "延迟突增与热点推理分片相关",
    severity: "critical",
    alerts: ["fp-001"],
  },
];

const summaries: DiagnosisSessionSummary[] = [
  {
    session_id: "sess-diagnosis-001",
    title: "existing diagnosis",
    summary: "诊断中",
    started_at: "2026-03-18T12:00:00Z",
    updated_at: "2026-03-18T12:05:00Z",
    status: "re_diagnosed",
    severity: "critical",
    alert_name: "VLLM 延迟过高",
    fingerprint: "fp-001",
    incident_key: "fpst:fp-001|2026-03-18T12:00:00Z",
    duration_seconds: 120,
    outcome: null,
    affected_services: [],
  },
  {
    session_id: "sess-remediation-002",
    title: "pending remediation",
    summary: "待审批",
    started_at: "2026-03-18T11:57:00Z",
    updated_at: "2026-03-18T12:06:00Z",
    status: "approval_required",
    severity: "warning",
    alert_name: "GPU 温度偏高",
    fingerprint: "fp-002",
    incident_key: "fpst:fp-002|2026-03-18T11:57:00Z",
    duration_seconds: 98,
    outcome: "proposed_fix_ready",
    affected_services: [],
  },
];

const diagnosisSession: DiagnosisSession = {
  session_id: "sess-diagnosis-001",
  alert: alerts[0]!,
  status: "re_diagnosed",
  duration_seconds: 120,
  diagnosis_result: {
    root_cause: [
      {
        id: "rc-gpu-hotspot",
        title: "热点分片上的 GPU 资源争用",
        layer: "hardware",
        entities: ["gpu-01"],
        confidence: 0.91,
        certainty: "confirmed",
        status: "confirmed",
        evidence_summary: "热点分片 GPU 占用与排队深度同时持续高位。",
        impact_summary: "影响集中在单个推理热点分片。",
        distinguishing_verification: "检查热点分片 GPU 进程占用与排队深度是否同步抬升。",
      },
    ],
    confidence: 0.91,
    hypotheses: [],
    impact_summary: "影响集中在单个推理热点分片。",
    affected_services: ["vllm"],
    triage_priority: "P1",
    diagnosis_certainty: "confirmed",
  },
};

const remediationSession: DiagnosisSession = {
  session_id: "sess-remediation-002",
  alert: alerts[1]!,
  status: "approval_required",
  duration_seconds: 98,
  diagnosis_result: {
    root_cause: [
      {
        id: "rc-cooling-drop",
        title: "机柜散热效率下降",
        layer: "hardware",
        entities: ["gpu-01"],
        confidence: 0.84,
        certainty: "probable",
        status: "confirmed",
        evidence_summary: "温度曲线与风扇档位偏离趋势一致。",
        impact_summary: "影响集中在单节点 GPU 温度抬升。",
        distinguishing_verification: "提升风扇档位后复测温度回落斜率。",
      },
    ],
    confidence: 0.84,
    hypotheses: [],
    impact_summary: "影响集中在单节点 GPU 温度抬升。",
    affected_services: ["embedding-serving"],
    triage_priority: "P2",
    diagnosis_certainty: "probable",
    recommended_fix: {
      plan_id: "plan-1",
      root_cause: "机柜散热效率下降",
      description: "先提升风扇档位，再观察温度曲线是否回落。",
      steps: [],
      estimated_impact: "low",
      confidence: 0.84,
      priority: "P2",
    },
  },
};

function renderAlertsModifiedPage() {
  return render(
    <MemoryRouter initialEntries={["/alerts"]}>
      <Routes>
        <Route path="/alerts" element={<AlertsModifiedPage />} />
        <Route path="/diagnosis/:sessionId" element={<div>diagnosis route</div>} />
        <Route path="/remediation" element={<div>remediation route</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AlertsModifiedPage", () => {
  beforeEach(() => {
    useAlertStore.setState({
      alerts: [],
      clusters: [],
      severityFilter: "all",
      isLoading: false,
      hasLoaded: false,
      error: undefined,
    });

    vi.spyOn(apiClient, "getAlerts").mockResolvedValue({ alerts, clusters });
    vi.spyOn(apiClient, "getDiagnosisHistorySessions").mockResolvedValue(summaries);
    vi.spyOn(apiClient, "getDiagnosisSession").mockImplementation(async (sessionId?: string) => {
      if (sessionId === "sess-diagnosis-001") {
        return diagnosisSession;
      }
      if (sessionId === "sess-remediation-002") {
        return remediationSession;
      }
      return null;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders an alert diagnosis list without aggregation-only labels", async () => {
    renderAlertsModifiedPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "查看诊断" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "审批修复" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "进入诊断" })).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText("机柜散热效率下降")).toBeInTheDocument();
      expect(screen.getByText("先提升风扇档位，再观察温度曲线是否回落。"))
        .toBeInTheDocument();
    });

    expect(screen.queryByText("原始告警流")).not.toBeInTheDocument();
    expect(screen.queryByText("聚合诊断")).not.toBeInTheDocument();
    expect(screen.queryByText(/fingerprint/i)).not.toBeInTheDocument();
    expect(screen.queryByText("关联告警")).not.toBeInTheDocument();
    expect(screen.queryByText("2 条事件")).not.toBeInTheDocument();
    expect(screen.getByText("尚未生成诊断结论")).toBeInTheDocument();
    expect(screen.getByText("待诊断")).toBeInTheDocument();
    expect(screen.getByText("告警工作项")).toBeInTheDocument();
    expect(screen.getByText("3 / 3 项")).toBeInTheDocument();
    expect(screen.queryByText("告警同步中")).not.toBeInTheDocument();
    expect(screen.queryByText("会话同步中")).not.toBeInTheDocument();
    expect(screen.queryByText("诊断补充中")).not.toBeInTheDocument();
    expect(screen.queryByText("已同步")).not.toBeInTheDocument();
    expect(screen.getByText("incident_key fpst:fp-001|2026-03-18T12:00:00Z")).toBeInTheDocument();
    expect(screen.queryByText(/event_id/i)).not.toBeInTheDocument();
  });
});
