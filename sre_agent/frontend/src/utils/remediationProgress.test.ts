import { describe, expect, it } from "vitest";

import type { RemediationOverview, SessionEvent } from "../api/types";
import {
  deriveBatchStatusFromTimeline,
  deriveRemediationExecutionView,
  getRemediationOverallProgressDisplay,
} from "./remediationProgress";

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
      steps: Array.from({ length: args.totalSteps ?? 0 }, (_, index) => ({
        step_id: index + 1,
        description: `step-${index + 1}`,
        tool: "tool",
        params: {},
        verification: { method: "wait", wait_seconds: 5 },
        timeout: 30,
      })),
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

  it("treats execution_succeeded as completed even when observation_result is missing", () => {
    const overview = createOverview({
      status: "execution_succeeded",
      completedSteps: 3,
      totalSteps: 3,
      timeline: [
        remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z"),
        remediationEvent("execution_succeeded", {}, "2026-04-21T10:08:00Z"),
      ],
    });
    expect(getRemediationOverallProgressDisplay(overview)).toBe(100);
  });

  it("keeps 100 when observation_result(pass) is followed by execution_succeeded", () => {
    const overview = createOverview({
      status: "resolved",
      completedSteps: 3,
      totalSteps: 3,
      timeline: [
        remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z"),
        remediationEvent(
          "observation_result",
          { alert_cleared: true, metrics_improved: true },
          "2026-04-21T10:07:00Z",
        ),
        remediationEvent("execution_succeeded", {}, "2026-04-21T10:08:00Z"),
      ],
    });
    expect(getRemediationOverallProgressDisplay(overview)).toBe(100);
  });

  it("keeps failed terminal stages below 100", () => {
    const overview = createOverview({
      status: "execution_failed",
      completedSteps: 1,
      totalSteps: 3,
      timeline: [
        remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z"),
        remediationEvent("execution_failed", {}, "2026-04-21T10:08:00Z"),
      ],
    });
    expect(getRemediationOverallProgressDisplay(overview)).toBeLessThan(100);
  });

  it("derives non-zero canary progress from batch events before all batches complete", () => {
    const overview = createOverview({
      status: "validating",
      completedSteps: 0,
      totalSteps: 4,
      timeline: [
        remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z"),
        remediationEvent(
          "canary_batch_started",
          { batch: "canary-1", batch_index: 1, batch_total: 2 },
          "2026-04-21T10:01:00Z",
        ),
        remediationEvent(
          "canary_batch_completed",
          { batch: "canary-1", batch_index: 1, batch_total: 2 },
          "2026-04-21T10:02:00Z",
        ),
        remediationEvent(
          "canary_batch_started",
          { batch: "canary-2", batch_index: 2, batch_total: 2 },
          "2026-04-21T10:03:00Z",
        ),
      ],
    });
    overview.plan.canary = {
      enabled: true,
      target_percentage: 0.5,
      monitor_duration: 120,
      max_batches: 2,
      success_criteria: [],
    };

    const executionView = deriveRemediationExecutionView({ overview, now: Date.parse("2026-04-21T10:03:30Z") });
    expect(executionView.canaryProgress).toBe(75);
    expect(executionView.overallProgress).toBeGreaterThan(10);
  });

  it("marks canary progress as complete only after all batches finish", () => {
    const overview = createOverview({
      status: "resolved",
      completedSteps: 4,
      totalSteps: 4,
      timeline: [
        remediationEvent("approval_accepted", {}, "2026-04-21T10:00:00Z"),
        remediationEvent(
          "canary_batch_completed",
          { batch: "canary-1", batch_index: 1, batch_total: 2 },
          "2026-04-21T10:02:00Z",
        ),
        remediationEvent(
          "canary_batch_completed",
          { batch: "canary-2", batch_index: 2, batch_total: 2 },
          "2026-04-21T10:05:00Z",
        ),
        remediationEvent("execution_succeeded", { steps_completed: 4 }, "2026-04-21T10:06:00Z"),
      ],
    });
    overview.plan.canary = {
      enabled: true,
      target_percentage: 0.5,
      monitor_duration: 120,
      max_batches: 2,
      success_criteria: [],
    };

    const batchStatus = deriveBatchStatusFromTimeline(overview);
    expect(batchStatus).toEqual([
      { batch: "canary-1", progress: 50, status: "resolved" },
      { batch: "canary-2", progress: 100, status: "resolved" },
    ]);
    expect(deriveRemediationExecutionView({ overview }).canaryProgress).toBe(100);
  });

  it("keeps live duration increasing until terminal state and freezes afterwards", () => {
    const inFlightOverview = createOverview({
      status: "remediating",
      completedSteps: 0,
      totalSteps: 1,
      timeline: [
        remediationEvent("execution_started", {}, "2026-04-21T10:00:00Z"),
      ],
    });
    const inFlightView = deriveRemediationExecutionView({
      overview: inFlightOverview,
      now: Date.parse("2026-04-21T10:00:12Z"),
    });
    expect(inFlightView.durationSeconds).toBe(12);

    const terminalOverview = createOverview({
      status: "execution_failed",
      completedSteps: 0,
      totalSteps: 1,
      timeline: [
        remediationEvent("execution_started", {}, "2026-04-21T10:00:00Z"),
        remediationEvent("execution_failed", {}, "2026-04-21T10:00:09Z"),
      ],
    });
    const terminalView = deriveRemediationExecutionView({
      overview: terminalOverview,
      now: Date.parse("2026-04-21T10:00:30Z"),
    });
    expect(terminalView.durationSeconds).toBe(9);
  });
});
