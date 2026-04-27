import { describe, expect, it } from "vitest";

import type { DiagnosisResult, SessionEvent } from "../api/types";
import { getPendingApprovalPlanKey, getPlanByPlanKey } from "./rootCauseModel";

function makeDiagnosisResult(): DiagnosisResult {
  return {
    root_cause: [
      {
        id: "rc-1",
        title: "GPU contention",
        layer: "service",
        entities: ["worker-03"],
        confidence: 0.95,
        certainty: "confirmed",
        status: "confirmed",
        evidence_summary: "gpu busy",
        impact_summary: "latency high",
        recommended_fix: {
          plan_id: "plan-rc-1",
          root_cause: "GPU contention",
          description: "repair gpu",
          steps: [],
          estimated_impact: "minor",
          confidence: 0.95,
          priority: "P1",
        },
      },
      {
        id: "rc-2",
        title: "External load",
        layer: "network",
        entities: ["10.11.4.13"],
        confidence: 0.9,
        certainty: "confirmed",
        status: "confirmed",
        evidence_summary: "load simulator",
        impact_summary: "latency high",
        recommended_fix: {
          plan_id: "plan-rc-2",
          root_cause: "External load",
          description: "repair load",
          steps: [],
          estimated_impact: "minor",
          confidence: 0.9,
          priority: "P1",
        },
      },
    ],
    confidence: 0.95,
    hypotheses: [],
    propagation_chain: [],
    impact_summary: "latency high",
    affected_services: ["vllm"],
    triage_priority: "P1",
    diagnosis_certainty: "confirmed",
  };
}

describe("rootCauseModel pending approval plan", () => {
  it("uses latest next-plan approval event before falling back to the primary plan", () => {
    const result = makeDiagnosisResult();
    const events: SessionEvent[] = [
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "s1",
        timestamp: "2026-04-27T00:00:00.000Z",
        data: {
          stage: "next_plan_approval_required",
          plan_key: "rc:rc-2",
        },
      },
    ];

    expect(getPendingApprovalPlanKey(events, result)).toBe("rc:rc-2");
    expect(getPlanByPlanKey(result, "rc:rc-2")?.plan_id).toBe("plan-rc-2");
    expect(getPendingApprovalPlanKey([], result)).toBe("rc:rc-1");
  });
});
