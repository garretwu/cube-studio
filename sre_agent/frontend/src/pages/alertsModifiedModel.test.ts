import { describe, expect, it } from "vitest";

import type { Alert, AlertCluster, DiagnosisSession, DiagnosisSessionSummary } from "../api/types";
import { buildAlertDashboardView } from "./alertsModifiedModel";

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
];

const clusters: AlertCluster[] = [
  {
    cluster_id: "cluster-01",
    summary: "延迟突增与单个推理节点的热压异常高度相关",
    severity: "critical",
    alerts: ["fp-001", "fp-002"],
  },
];

const summaries: DiagnosisSessionSummary[] = [
  {
    session_id: "sess-diagnosis-001",
    title: "existing diagnosis",
    summary: "诊断中",
    started_at: "2026-03-18T12:00:10Z",
    updated_at: "2026-03-18T12:05:00Z",
    status: "re_diagnosed",
    severity: "critical",
    alert_name: "VLLM 延迟过高",
    fingerprint: "fp-001",
    duration_seconds: 120,
    outcome: null,
    affected_services: [],
  },
  {
    session_id: "sess-remediation-002",
    title: "pending remediation",
    summary: "待执行",
    started_at: "2026-03-18T11:57:10Z",
    updated_at: "2026-03-18T12:06:00Z",
    status: "approval_required",
    severity: "warning",
    alert_name: "GPU 温度偏高",
    fingerprint: "fp-002",
    duration_seconds: 98,
    outcome: "proposed_fix_ready",
    affected_services: [],
  },
];

const details: Record<string, DiagnosisSession> = {
  "sess-remediation-002": {
    session_id: "sess-remediation-002",
    alert: alerts[1]!,
    status: "approval_required",
    duration_seconds: 98,
    diagnosis_result: {
      root_cause: "机柜散热效率下降",
      root_cause_layer: "hardware",
      root_cause_entities: ["gpu-01"],
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
  },
};

describe("buildAlertDashboardView", () => {
  it("prefers remediation-linked summaries when a cluster matches multiple fingerprints", () => {
    const view = buildAlertDashboardView(alerts, clusters, summaries, details, new Date("2026-03-18T12:30:00Z"));

    expect(view.items).toHaveLength(1);
    expect(view.items[0]?.statusKey).toBe("pending_remediation");
    expect(view.items[0]?.action.kind).toBe("remediation");
    expect(view.items[0]?.sessionId).toBe("sess-remediation-002");
    expect(view.items[0]?.analysisSummary).toBe("机柜散热效率下降");
    expect(view.items[0]?.planSummary).toContain("提升风扇档位");
    expect(view.metrics.totalItems).toBe(1);
    expect(view.metrics.pendingActionCount).toBe(1);
  });
});
