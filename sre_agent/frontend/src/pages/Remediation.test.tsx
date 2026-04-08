import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisSessionSummary, RemediationOverview, SessionEvent } from "../api/types";
import RemediationPage from "./Remediation";

const mockedApi = vi.hoisted(() => ({
  getDiagnosisHistorySessions: vi.fn(async (): Promise<DiagnosisSessionSummary[]> => []),
  getRemediationOverview: vi.fn(async (_sessionId: string): Promise<RemediationOverview> => {
    throw new Error("not implemented");
  }),
}));

const mockedStore = vi.hoisted(() => ({
  fetchOverview: vi.fn(async () => undefined),
  setSessionId: vi.fn(),
  submitApproval: vi.fn(async () => undefined),
  setApprovalDialogOpen: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiClient: {
    getDiagnosisHistorySessions: mockedApi.getDiagnosisHistorySessions,
    getRemediationOverview: mockedApi.getRemediationOverview,
  },
}));

vi.mock("../store/remediationStore", () => ({
  useRemediationStore: () => ({
    overview: undefined,
    events: [],
    approvalDialogOpen: false,
    isLoading: false,
    fetchOverview: mockedStore.fetchOverview,
    setApprovalDialogOpen: mockedStore.setApprovalDialogOpen,
    submitApproval: mockedStore.submitApproval,
    setSessionId: mockedStore.setSessionId,
  }),
}));

function buildTimeline(stepDescription: string): SessionEvent[] {
  return [
    {
      schema_version: "1.0",
      type: "diagnosis_result",
      session_id: "sess-1",
      timestamp: "2026-04-03T10:00:00Z",
      data: {},
    },
    {
      schema_version: "1.0",
      type: "approval_required",
      session_id: "sess-1",
      timestamp: "2026-04-03T10:01:00Z",
      data: { plan_version: 2 },
    },
    {
      schema_version: "1.0",
      type: "remediation_progress",
      session_id: "sess-1",
      timestamp: "2026-04-03T10:01:30Z",
      data: {
        stage: "pre_remediation_baseline_collected",
        baseline_alert: { alert_name: "vllm_latency_high", status: "firing", is_firing: true },
        baseline_metrics: [{ metric_key: "llm_latency_p95", value: 620 }],
      },
    },
    {
      schema_version: "1.0",
      type: "remediation_progress",
      session_id: "sess-1",
      timestamp: "2026-04-03T10:01:45Z",
      data: {
        stage: "observation_result",
        alert_cleared: true,
        metrics_improved: true,
        metric_reviews: [{ metric_key: "llm_latency_p95", before_value: 620, after_value: 210, improved: true, available: true }],
      },
    },
    {
      schema_version: "1.0",
      type: "remediation_progress",
      session_id: "sess-1",
      timestamp: "2026-04-03T10:02:00Z",
      data: {
        stage: "execution_succeeded",
        steps_completed: 1,
        step_results: [{ step_id: 1, tool: "k8s.restart_deployment", command: "rollout restart", success: true }],
      },
    },
  ];
}

function buildOverview(sessionId: string, stepDescription: string): RemediationOverview {
  return {
    session_id: sessionId,
    approval_required: false,
    plan: {
      plan_id: `plan-${sessionId}`,
      root_cause: "cpu contention",
      description: "one-shot remediation",
      estimated_impact: "low",
      confidence: 0.8,
      priority: "P1",
      steps: [
        {
          step_id: 1,
          description: stepDescription,
          tool: "k8s.restart_deployment",
          params: { namespace: "svc", name: "api" },
          verification: { method: "wait", wait_seconds: 10 },
          timeout: 60,
        },
      ],
    },
    progress: {
      status: "resolved",
      completed_steps: 1,
      total_steps: 1,
      batch_status: [{ batch: "一次性执行", progress: 100, status: "resolved" }],
    },
    baseline_review: {
      pre_check: {
        collected_at: "2026-04-03T10:01:30Z",
        alert: {
          fingerprint: "fp-1",
          alert_name: "vllm_latency_high",
          status: "firing",
          is_firing: true,
          collected_at: "2026-04-03T10:01:30Z",
          available: true,
        },
        metrics: [{ metric_key: "llm_latency_p95", query: "vllm_request_latency_p95", value: 620, collected_at: "2026-04-03T10:01:30Z", available: true }],
      },
      post_check: {
        collected_at: "2026-04-03T10:01:45Z",
        alert: {
          fingerprint: "fp-1",
          alert_name: "vllm_latency_high",
          status: "resolved",
          is_firing: false,
          collected_at: "2026-04-03T10:01:45Z",
          available: true,
        },
        metrics: [{ metric_key: "llm_latency_p95", query: "vllm_request_latency_p95", value: 210, collected_at: "2026-04-03T10:01:45Z", available: true }],
      },
      alert_review: {
        fingerprint: "fp-1",
        alert_name: "vllm_latency_high",
        before_status: "firing",
        after_status: "resolved",
        cleared: true,
        reviewed_at: "2026-04-03T10:01:45Z",
      },
      metric_reviews: [{ metric_key: "llm_latency_p95", query: "vllm_request_latency_p95", before_value: 620, after_value: 210, improved: true, available: true, reviewed_at: "2026-04-03T10:01:45Z" }],
      alert_cleared: true,
      metrics_improved: true,
      collected_at: "2026-04-03T10:01:45Z",
    },
    timeline: buildTimeline(stepDescription),
  };
}

function renderWithRoute(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/remediation" element={<RemediationPage />} />
        <Route path="/remediation/:sessionId" element={<RemediationPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RemediationPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mockedApi.getDiagnosisHistorySessions.mockResolvedValue([
      {
        session_id: "sess-1",
        title: "vllm latency · WARNING",
        summary: "hot node overload",
        started_at: "2026-04-03T10:00:00Z",
        updated_at: "2026-04-03T10:10:00Z",
        status: "resolved",
        severity: "warning",
        alert_name: "vllm_latency_high",
        duration_seconds: 120,
        outcome: "resolved",
        triage_priority: "P1",
        root_cause: "cpu contention",
        affected_services: ["inference-gateway"],
      },
      {
        session_id: "sess-2",
        title: "traffic skew · WARNING",
        summary: "router imbalance",
        started_at: "2026-04-03T09:00:00Z",
        updated_at: "2026-04-03T09:10:00Z",
        status: "resolved",
        severity: "warning",
        alert_name: "traffic_skew",
        duration_seconds: 60,
        outcome: "resolved",
        triage_priority: "P2",
        root_cause: "router config mismatch",
        affected_services: ["route-layer"],
      },
    ]);

    mockedApi.getRemediationOverview.mockImplementation(async (sessionId: string) => {
      if (sessionId === "sess-2") {
        return buildOverview(sessionId, "scale deployment");
      }
      return buildOverview(sessionId, "restart deployment");
    });
  });

  it("renders current table + drawer architecture and step details", async () => {
    const user = userEvent.setup();
    renderWithRoute("/remediation");

    expect(screen.getByRole("heading", { name: "修复与执行" })).toBeInTheDocument();

    await waitFor(() => {
      expect(mockedStore.setSessionId).toHaveBeenCalledWith("sess-1");
      expect(mockedStore.fetchOverview).toHaveBeenCalledWith("sess-1");
    });

    expect(await screen.findByRole("dialog", { name: "修复详情" })).toBeInTheDocument();
    expect(screen.getByText("方案信息")).toBeInTheDocument();
    expect(screen.getByText("执行步骤")).toBeInTheDocument();

    expect(screen.getByText("基线与复查")).toBeInTheDocument();
    expect(screen.getByText("告警已清除")).toBeInTheDocument();
    expect(screen.getByText("620 -> 210")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看步骤 1 详情" }));
    expect(screen.getByText("步骤 1 详情")).toBeInTheDocument();
    expect(screen.getAllByText("restart deployment").length).toBeGreaterThan(0);
  });

  it("supports /remediation/:sessionId deep-link selection", async () => {
    renderWithRoute("/remediation/sess-2");

    await waitFor(() => {
      expect(mockedStore.setSessionId).toHaveBeenCalledWith("sess-2");
      expect(mockedStore.fetchOverview).toHaveBeenCalledWith("sess-2");
    });

    expect(await screen.findByRole("dialog", { name: "修复详情" })).toBeInTheDocument();
    expect(screen.getByText("scale deployment")).toBeInTheDocument();
  });
});
