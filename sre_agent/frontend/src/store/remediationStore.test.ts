import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "../api/client";
import type { RemediationOverview } from "../api/types";
import { useRemediationStore } from "./remediationStore";

vi.mock("../api/client", () => ({
  apiClient: {
    getRemediationOverview: vi.fn(),
    getSessionLoop: vi.fn(),
    approveRemediation: vi.fn(),
  },
}));

const remediationOverviewFixture: RemediationOverview = {
  session_id: "sess-1",
  plan_version: 2,
  approval_required: false,
  plan: {
    plan_id: "plan-1",
    root_cause: "cpu contention",
    description: "apply one-shot mitigation",
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
  timeline: [
    {
      schema_version: "1.0",
      type: "remediation_progress",
      session_id: "sess-1",
      timestamp: "2026-04-03T10:00:00Z",
      data: { stage: "execution_succeeded", steps_completed: 1 },
    },
  ],
};

describe("remediationStore", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useRemediationStore.setState({
      loop: undefined,
      overview: undefined,
      events: [],
      sessionId: "",
      isLoading: false,
      approvalDialogOpen: false,
    });
  });

  it("fetches overview when explicit session id is provided", async () => {
    vi.mocked(apiClient.getRemediationOverview).mockResolvedValue(remediationOverviewFixture);

    await useRemediationStore.getState().fetchOverview("sess-1");

    const state = useRemediationStore.getState();
    expect(apiClient.getRemediationOverview).toHaveBeenCalledWith("sess-1");
    expect(state.overview?.session_id).toBe("sess-1");
    expect(state.events).toHaveLength(1);
    expect(state.sessionId).toBe("sess-1");
  });

  it("keeps refreshing overview when getSessionLoop fails after approval", async () => {
    vi.mocked(apiClient.approveRemediation).mockResolvedValue({
      plan_id: "plan-1",
      success: true,
      steps_completed: 1,
      steps_total: 1,
      duration_seconds: 5,
    });
    vi.mocked(apiClient.getRemediationOverview).mockResolvedValue(remediationOverviewFixture);
    vi.mocked(apiClient.getSessionLoop).mockRejectedValue(new Error("loop not found"));

    useRemediationStore.setState({
      sessionId: "sess-1",
      overview: remediationOverviewFixture,
      approvalDialogOpen: true,
    });

    await expect(useRemediationStore.getState().submitApproval(true)).resolves.toBeUndefined();

    const state = useRemediationStore.getState();
    expect(apiClient.approveRemediation).toHaveBeenCalledWith("sess-1", true, "ui-operator", 2);
    expect(apiClient.getSessionLoop).toHaveBeenCalledWith("sess-1");
    expect(state.overview?.session_id).toBe("sess-1");
    expect(state.events).toHaveLength(1);
    expect(state.approvalDialogOpen).toBe(false);
  });
});
