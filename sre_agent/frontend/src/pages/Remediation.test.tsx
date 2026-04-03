import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RemediationPage from "./Remediation";

const fetchOverview = vi.fn(async () => undefined);
const setSessionId = vi.fn();

vi.mock("../api/client", () => ({
  apiClient: {
    getSessions: vi.fn(async () => [
      {
        session_id: "sess-1",
        status: "resolved",
        alert_name: "vllm_latency_high",
        severity: "warning",
        fingerprint: "fp-1",
        duration_seconds: 60,
        updated_at: "2026-04-03T10:00:00Z",
      },
    ]),
  },
}));

vi.mock("../store/remediationStore", () => ({
  useRemediationStore: () => ({
    overview: {
      session_id: "sess-1",
      approval_required: false,
      plan: {
        plan_id: "plan-1",
        root_cause: "cpu contention",
        description: "one-shot remediation",
        estimated_impact: "low",
        confidence: 0.8,
        priority: "P1",
        steps: [
          {
            step_id: 1,
            description: "restart deployment",
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
    },
    events: [
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
        timestamp: "2026-04-03T10:02:00Z",
        data: { stage: "execution_succeeded", steps_completed: 1 },
      },
    ],
    approvalDialogOpen: false,
    fetchOverview,
    setApprovalDialogOpen: vi.fn(),
    submitApproval: vi.fn(async () => undefined),
    setSessionId,
  }),
}));

describe("RemediationPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders session selector and remediation-only timeline", async () => {
    render(
      <MemoryRouter initialEntries={["/remediation"]}>
        <Routes>
          <Route path="/remediation" element={<RemediationPage />} />
          <Route path="/remediation/:sessionId" element={<RemediationPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "修复执行关口" })).toBeInTheDocument();
    expect(screen.getByText("会话选择")).toBeInTheDocument();

    await waitFor(() => {
      expect(fetchOverview).toHaveBeenCalled();
      expect(setSessionId).toHaveBeenCalledWith("sess-1");
    });

    expect(screen.getByText("等待审批")).toBeInTheDocument();
    expect(screen.getByText("修复执行成功")).toBeInTheDocument();
    expect(screen.queryByText("diagnosis_result")).not.toBeInTheDocument();
  });
});
