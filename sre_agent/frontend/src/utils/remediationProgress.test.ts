import { describe, expect, it } from "vitest";

import type { RemediationOverview, SessionEvent } from "../api/types";
import { getRemediationOverallProgressDisplay } from "./remediationProgress";

function createOverview(args: {
  status?: string;
  completedSteps?: number;
  totalSteps?: number;
  timeline?: SessionEvent[];
}): RemediationOverview {
  return {
    session_id: "sess-test",
    plan: {
      plan_id: "plan-test",
      root_cause: "root",
      description: "desc",
      steps: [],
      estimated_impact: "impact",
      confidence: 0.8,
      priority: "P1",
    },
    progress: {
      status: args.status ?? "pending",
      completed_steps: args.completedSteps ?? 0,
      total_steps: args.totalSteps ?? 0,
      batch_status: [],
    },
    timeline: args.timeline ?? [],
    approval_required: true,
  };
}

function remediationEvent(stage: string, data: Record<string, unknown> = {}, timestamp = "2026-04-21T10:00:00Z"): SessionEvent {
  return {
    schema_version: "1.0",
    type: "remediation_progress",
    session_id: "sess-test",
    timestamp,
    data: {
      stage,
      ...data,
    },
  };
}

describe("getRemediationOverallProgressDisplay", () => {
  it("returns 0 before approval is accepted", () => {
    const overview = createOverview({ status: "approval_required", completedSteps: 1, totalSteps: 2 });
    expect(getRemediationOverallProgressDisplay(overview)).toBe(0);
  });

  it("applies approval and execution weights", () => {
    const overview = createOverview({
      status: "remediating",
      completedSteps: 1,
      totalSteps: 2,
      timeline: [remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z")],
    });
    expect(getRemediationOverallProgressDisplay(overview)).toBe(45);
  });

  it("adds observation weight only when alert and metrics both pass", () => {
    const overview = createOverview({
      status: "resolved",
      completedSteps: 2,
      totalSteps: 2,
      timeline: [
        remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z"),
        remediationEvent("observation_result", { alert_cleared: true, metrics_improved: true }, "2026-04-21T10:10:00Z"),
      ],
    });
    expect(getRemediationOverallProgressDisplay(overview)).toBe(100);
  });

  it("uses alert-only policy when post metrics are unavailable", () => {
    const overview = createOverview({
      status: "resolved",
      completedSteps: 2,
      totalSteps: 2,
      timeline: [
        remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z"),
        remediationEvent(
          "observation_result",
          {
            alert_cleared: true,
            policy_applied: "alert_status_only_when_post_metrics_unavailable",
          },
          "2026-04-21T10:10:00Z",
        ),
      ],
    });
    expect(getRemediationOverallProgressDisplay(overview)).toBe(100);
  });

  it("keeps observation contribution at 0 when observation_result is missing", () => {
    const overview = createOverview({
      status: "execution_succeeded",
      completedSteps: 3,
      totalSteps: 3,
      timeline: [
        remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z"),
        remediationEvent("execution_succeeded", {}, "2026-04-21T10:08:00Z"),
      ],
    });
    expect(getRemediationOverallProgressDisplay(overview)).toBe(80);
  });
});
