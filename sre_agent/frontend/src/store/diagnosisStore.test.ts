import { beforeEach, describe, expect, it } from "vitest";

import type { DiagnosisSession } from "../api/types";
import { useDiagnosisStore } from "./diagnosisStore";

const baseSession: DiagnosisSession = {
  session_id: "session-1",
  alert: {
    alert_name: "GPUUtilizationHigh",
    severity: "warning",
    labels: { node: "node-a" },
    annotations: {},
    starts_at: "2026-03-27T00:00:00Z",
    ends_at: null,
    fingerprint: "fp-session-1",
    status: "firing",
    source: "test",
  },
  status: "diagnosing",
  diagnosis_result: null,
  trace: { steps: [] },
  duration_seconds: 0,
  outcome: null,
};

describe("diagnosisStore.applyEvent", () => {
  beforeEach(() => {
    useDiagnosisStore.setState({
      session: structuredClone(baseSession),
      sessionId: baseSession.session_id,
      seenEventIds: {},
      connectionState: "open",
    });
  });

  it("appends live tool_call/tool_result/thinking_step events and updates diagnosis_result", () => {
    const store = useDiagnosisStore.getState();
    store.applyEvent({
      schema_version: "1.0",
      type: "tool_call",
      session_id: baseSession.session_id,
      timestamp: "2026-03-27T00:00:01Z",
      data: {
        event_id: "1",
        step: 1,
        thought: "collect gpu metrics",
        action_type: "tool_call",
        tool_name: "gpu.get_metrics",
        tool_params: { node: "node-a" },
      },
    });
    store.applyEvent({
      schema_version: "1.0",
      type: "tool_result",
      session_id: baseSession.session_id,
      timestamp: "2026-03-27T00:00:02Z",
      data: {
        event_id: "2",
        tool: "gpu.get_metrics",
        params: { node: "node-a" },
        result: { success: true, data: { utilization: 97 } },
      },
    });
    store.applyEvent({
      schema_version: "1.0",
      type: "thinking_step",
      session_id: baseSession.session_id,
      timestamp: "2026-03-27T00:00:03Z",
      data: {
        event_id: "3",
        step: 2,
        thought: "gpu contention is likely",
        action_type: "conclude",
        confidence: 0.9,
      },
    });
    store.applyEvent({
      schema_version: "1.0",
      type: "diagnosis_result",
      session_id: baseSession.session_id,
      timestamp: "2026-03-27T00:00:04Z",
      data: {
        event_id: "4",
        root_cause: "gpu contention",
        root_cause_layer: "service",
        root_cause_entities: ["node-a"],
        confidence: 0.9,
        hypotheses: [],
        impact_summary: "latency spike",
        affected_services: ["vllm"],
        triage_priority: "P1",
        diagnosis_certainty: "confirmed",
      },
    });

    const next = useDiagnosisStore.getState().session;
    expect(next?.trace?.steps).toHaveLength(3);
    expect(next?.trace?.steps[0]).toMatchObject({ action_type: "tool_call", tool_name: "gpu.get_metrics" });
    expect(next?.trace?.steps[1]).toMatchObject({ tool: "gpu.get_metrics" });
    expect(next?.trace?.steps[2]).toMatchObject({ action_type: "conclude" });
    expect(next?.diagnosis_result?.root_cause).toBe("gpu contention");
  });

  it("deduplicates websocket events with the same event_id", () => {
    const store = useDiagnosisStore.getState();
    const duplicateEvent = {
      schema_version: "1.0",
      type: "thinking_step" as const,
      session_id: baseSession.session_id,
      timestamp: "2026-03-27T00:00:05Z",
      data: {
        event_id: "dup-1",
        step: 1,
        thought: "duplicate",
        action_type: "conclude",
      },
    };
    store.applyEvent(duplicateEvent);
    store.applyEvent(duplicateEvent);

    const next = useDiagnosisStore.getState().session;
    expect(next?.trace?.steps).toHaveLength(1);
  });
});
