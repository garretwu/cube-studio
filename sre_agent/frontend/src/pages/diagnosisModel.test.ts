import { describe, expect, it } from "vitest";

import type { Alert, DiagnosisSession } from "../api/types";
import {
  buildDiagnosisDemoScenario,
  buildDiagnosisLiveView,
  normalizeDiagnosisDisplayText,
  type DiagnosisTimelineItem,
} from "./diagnosisModel";
import { formatDateTimeParts } from "../utils/format";

const baseAlert: Alert = {
  alert_name: "Latency spike",
  severity: "critical",
  labels: { service: "auth-svc" },
  annotations: { summary: "p95 latency is high" },
  starts_at: "2026-04-08T10:00:00.000Z",
  fingerprint: "fp-live-1",
  status: "firing",
  source: "alertmanager",
};

function createSession(traceSteps: NonNullable<DiagnosisSession["trace"]>["steps"]): DiagnosisSession {
  return {
    session_id: "sess-live-1",
    alert: baseAlert,
    status: "diagnosing",
    duration_seconds: 0,
    trace: {
      steps: traceSteps,
    },
  };
}

describe("buildDiagnosisLiveView tool matching", () => {
  it("matches non-adjacent observations by tool name and resolves into single cards", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:00:01.000Z",
        thought: "Call metrics",
        action_type: "tool_call",
        tool_name: "query_metrics",
        tool_params: { service: "auth-svc" },
      },
      {
        step: 2,
        timestamp: "2026-04-08T10:00:02.000Z",
        thought: "Call logs",
        action_type: "tool_call",
        tool_name: "query_logs",
        tool_params: { service: "auth-svc" },
      },
      {
        tool: "query_logs",
        params: { service: "auth-svc" },
        result: { log_hits: 12 },
        timestamp: "2026-04-08T10:00:03.000Z",
      },
      {
        tool: "query_metrics",
        params: { service: "auth-svc" },
        result: { p95: "5.2s" },
        timestamp: "2026-04-08T10:00:04.000Z",
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);
    const toolItems = view.timeline.filter((item) => item.kind === "tool");

    expect(toolItems).toHaveLength(2);
    expect(toolItems.every((item) => item.status === "success")).toBe(true);

    const metricsCard = toolItems.find((item) => item.toolName === "query_metrics");
    const logsCard = toolItems.find((item) => item.toolName === "query_logs");

    expect(metricsCard?.summaryLines.join(" ")).toContain("p95");
    expect(logsCard?.summaryLines.join(" ")).toContain("log_hits");
  });

  it("does not keep a stale loading card and create a duplicate success card for delayed observation", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:10:01.000Z",
        thought: "Call deployment info",
        action_type: "tool_call",
        tool_name: "get_recent_deployments",
        tool_params: { service: "auth-svc" },
      },
      {
        step: 2,
        timestamp: "2026-04-08T10:10:02.000Z",
        thought: "Analyze while waiting",
        action_type: "conclude",
      },
      {
        tool: "get_recent_deployments",
        params: { service: "auth-svc" },
        result: { version: "v1.2.3" },
        timestamp: "2026-04-08T10:10:05.000Z",
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);
    const deploymentTools = view.timeline
      .filter((item): item is Extract<DiagnosisTimelineItem, { kind: "tool" }> => item.kind === "tool")
      .filter((item) => item.toolName === "get_recent_deployments");

    expect(deploymentTools).toHaveLength(1);
    expect(deploymentTools[0]?.status).toBe("success");
    expect(deploymentTools[0]?.summaryLines.join(" ")).toContain("version");
  });

  it("creates an orphan tool card only when no pending tool can be matched", () => {
    const session = createSession([
      {
        tool: "query_orphan_observation",
        params: { service: "auth-svc" },
        result: { note: "standalone observation" },
        timestamp: "2026-04-08T10:20:01.000Z",
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);
    const toolItems = view.timeline.filter((item) => item.kind === "tool");

    expect(toolItems).toHaveLength(1);
    expect(toolItems[0]?.toolName).toBe("query_orphan_observation");
    expect(toolItems[0]?.status).toBe("success");
  });

  it("keeps unmatched pending tools in loading state for UI timeout handling", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:30:01.000Z",
        thought: "Call slow tool",
        action_type: "tool_call",
        tool_name: "query_slow_backend",
        tool_params: { service: "auth-svc" },
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);
    const toolItems = view.timeline.filter((item) => item.kind === "tool");

    expect(toolItems).toHaveLength(1);
    expect(toolItems[0]?.status).toBe("loading");
    expect(toolItems[0]?.summaryLines).toEqual(["Waiting for tool result..."]);
  });
});

describe("buildDiagnosisLiveView next-action narration", () => {
  it("does not synthesize next-action assistant messages from thinking steps", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:40:01.000Z",
        thought: "Call metrics before deciding",
        action_type: "tool_call",
        tool_name: "query_metrics",
        tool_params: { service: "auth-svc" },
      },
      {
        tool: "query_metrics",
        params: { service: "auth-svc" },
        result: { p95: "5.2s" },
        timestamp: "2026-04-08T10:40:03.000Z",
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);

    expect(view.timeline[0]?.kind).toBe("thinking");
    expect(view.timeline[1]?.kind).toBe("tool");
    expect(view.timeline.filter((item) => item.kind === "message" && item.label === "Next action")).toHaveLength(0);
  });

  it("renders next-action from backend diagnosis_result without front-end templating", () => {
    const session: DiagnosisSession = {
      ...createSession([
        {
          step: 1,
          timestamp: "2026-04-08T10:50:01.000Z",
          thought: "Evidence is sufficient to conclude",
          action_type: "conclude",
        },
      ]),
      diagnosis_result: {
        root_cause: "GPU contention",
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03"],
        confidence: 0.9,
        next_action: "Use canary drain on worker-03 and validate p95 before full rollout.",
        hypotheses: [],
        impact_summary: "impact",
        affected_services: ["auth-svc"],
        triage_priority: "P1",
        diagnosis_certainty: "confirmed",
      },
    };

    const view = buildDiagnosisLiveView(session, []);

    expect(view.timeline[0]?.kind).toBe("thinking");

    const nextAction = view.timeline.find(
      (item): item is Extract<DiagnosisTimelineItem, { kind: "message" }> =>
        item.kind === "message" && item.label === "Next action",
    );

    expect(nextAction).toBeDefined();
    expect(nextAction?.role).toBe("assistant");
    expect(nextAction?.content).toBe("Use canary drain on worker-03 and validate p95 before full rollout.");
  });
});
describe("diagnosis report timeline item", () => {
  it("places the report item in the timeline before remediation system events", () => {
    const session: DiagnosisSession = {
      ...createSession([
        {
          step: 1,
          timestamp: "2026-04-08T10:55:01.000Z",
          thought: "Evidence is sufficient to conclude",
          action_type: "conclude",
        },
      ]),
      status: "approval_required",
      diagnosis_result: {
        root_cause: "GPU contention",
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03"],
        confidence: 0.91,
        hypotheses: [
          {
            description: "GPU contention",
            status: "confirmed",
            evidence_for: ["GPU util remains above 99%"],
            evidence_against: [],
            confidence: 0.91,
          },
        ],
        impact_summary: "impact",
        affected_services: ["auth-svc"],
        triage_priority: "P1",
        diagnosis_certainty: "confirmed",
        recommended_fix: {
          plan_id: "plan-gpu-v3",
          root_cause: "GPU contention",
          description: "Drain the hot node first",
          steps: [
            {
              step_id: 1,
              description: "Drain worker-03 canary",
              tool: "kubectl",
              params: { node: "worker-03" },
              verification: { method: "wait", wait_seconds: 60 },
              timeout: 120,
            },
          ],
          estimated_impact: "low",
          confidence: 0.91,
          priority: "P1",
        },
      },
    };

    const events = [
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: session.session_id,
        timestamp: "2026-04-08T10:55:15.000Z",
        data: {
          stage: "execution_started",
          approver: "alice",
          plan_version: 3,
          message: "begin remediation",
        },
      },
    ];

    const view = buildDiagnosisLiveView(session, [], events, []);
    const reportIndex = view.timeline.findIndex((item) => item.kind === "report");
    const systemIndex = view.timeline.findIndex((item) => item.kind === "system");

    expect(reportIndex).toBeGreaterThan(-1);
    expect(systemIndex).toBeGreaterThan(reportIndex);

    const reportItem = view.timeline[reportIndex];
    if (reportItem?.kind === "report") {
      expect(reportItem.summary.rootCause).toBe("GPU contention");
      expect(reportItem.planStatusLabel).toContain("\u5ba1\u6279");
    }
  });

  it("emits the demo report item before the complete event", () => {
    const scenario = buildDiagnosisDemoScenario("Analyze auth-svc latency spike");
    const reportAppendIndex = scenario.events.findIndex(
      (event) => event.type === "append" && event.item.kind === "report",
    );
    const completeIndex = scenario.events.findIndex(
      (event) => event.type === "complete",
    );

    expect(reportAppendIndex).toBeGreaterThan(-1);
    expect(completeIndex).toBeGreaterThan(reportAppendIndex);
  });
});

describe("buildDiagnosisDemoScenario ReAct cadence", () => {
  it("ensures every thinking append is followed by an assistant conclusion append", () => {
    const scenario = buildDiagnosisDemoScenario("Analyze auth-svc latency and error-rate spike");
    const appendItems = scenario.events
      .filter((event): event is Extract<(typeof scenario.events)[number], { type: "append" }> => event.type === "append")
      .map((event) => event.item);

    const thinkingIndices = appendItems
      .map((item, index) => (item.kind === "thinking" ? index : -1))
      .filter((index) => index >= 0);

    expect(thinkingIndices.length).toBeGreaterThan(0);

    for (const thinkingIndex of thinkingIndices) {
      const nextItem = appendItems[thinkingIndex + 1];
      expect(nextItem).toBeDefined();
      expect(nextItem?.kind).toBe("message");
      if (nextItem?.kind === "message") {
        expect(nextItem.role).toBe("assistant");
        expect(nextItem.content.trim().length).toBeGreaterThan(0);
      }
    }
  });
});


describe("diagnosis display text normalization", () => {
  it("decodes literal unicode escapes and mojibake in diagnosis summary fields", () => {
    const mojibakeImpact = String.fromCharCode(0xe4, 0xb8, 0xbb, 0xe8, 0xa6, 0x81, 0xe5, 0xbd, 0xb1, 0xe5, 0x93, 0x8d);

    expect(normalizeDiagnosisDisplayText(String.raw`\u6839\u56e0\uff1aworker-03`)).toBe("\u6839\u56e0\uff1aworker-03");
    expect(normalizeDiagnosisDisplayText(mojibakeImpact)).toBe("\u4e3b\u8981\u5f71\u54cd");

    const session: DiagnosisSession = {
      session_id: "sess-garbled-1",
      alert: baseAlert,
      status: "diagnosing",
      duration_seconds: 0,
      diagnosis_result: {
        root_cause: String.raw`\u5f53\u524d\u7ed3\u8bba\u4e3a worker-03 \u8282\u70b9 GPU \u4e89\u7528`,
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03", "gpu:0"],
        confidence: 0.91,
        hypotheses: [
          {
            description: String.raw`GPU \u4e89\u7528`,
            status: "confirmed",
            evidence_for: [String.raw`GPU util \u6301\u7eed 99%`],
            evidence_against: [],
            confidence: 0.91,
          },
        ],
        impact_summary: String.fromCharCode(
          0x76, 0x4c, 0x4c, 0x4d, 0x20, 0x70, 0x39, 0x35, 0x20,
          0xe5, 0xbb, 0xb6, 0xe8, 0xbf, 0x9f, 0xe6, 0x8c, 0x81, 0xe7, 0xbb, 0xad,
          0xe8, 0xb5, 0xb0, 0xe9, 0xab, 0x98, 0xef, 0xbc, 0x8c,
          0xe6, 0x8e, 0xa8, 0xe7, 0x90, 0x86, 0xe5, 0x90, 0x9e, 0xe5, 0x90, 0x90,
          0xe5, 0x87, 0xba, 0xe7, 0x8e, 0xb0, 0xe6, 0x98, 0x8e, 0xe6, 0x98, 0xbe,
          0xe4, 0xb8, 0x8b, 0xe9, 0x99, 0x8d, 0xe3, 0x80, 0x82,
        ),
        affected_services: ["inference-gateway", "vllm-serving"],
        triage_priority: "P1",
        diagnosis_certainty: "confirmed",
        ranked_candidates: [
          {
            rank: 1,
            root_cause: String.raw`GPU \u4e89\u7528`,
            root_cause_layer: "platform",
            root_cause_entities: ["node:worker-03", "gpu:0"],
            confidence: 0.91,
            evidence_summary: String.raw`GPU util \u6301\u7eed 99%\uff0c\u8bf7\u6c42\u6392\u961f\u65f6\u957f\u4e0e\u8d85\u65f6\u544a\u8b66\u540c\u6b65\u51fa\u73b0\u3002`,
            distinguishing_verification: String.raw`\u786e\u8ba4 worker-03 \u7684 GPU \u5f02\u5e38\u5360\u7528`,
          },
        ],
      },
    };

    const view = buildDiagnosisLiveView(session, []);

    expect(view.summary?.rootCause).toBe("\u5f53\u524d\u7ed3\u8bba\u4e3a worker-03 \u8282\u70b9 GPU \u4e89\u7528");
    expect(view.summary?.impactSummary).toBe("vLLM p95 \u5ef6\u8fdf\u6301\u7eed\u8d70\u9ad8\uff0c\u63a8\u7406\u541e\u5410\u51fa\u73b0\u660e\u663e\u4e0b\u964d\u3002");
    expect(view.candidates[0]?.title).toBe("GPU \u4e89\u7528");
    expect(view.candidates[0]?.evidenceSummary).toBe("GPU util \u6301\u7eed 99%\uff0c\u8bf7\u6c42\u6392\u961f\u65f6\u957f\u4e0e\u8d85\u65f6\u544a\u8b66\u540c\u6b65\u51fa\u73b0\u3002");
  });
});


describe("diagnosis summary metadata", () => {
  it("prefers the latest timeline timestamp when deriving the updated time labels", () => {
    const session: DiagnosisSession = {
      session_id: "sess-summary-1",
      alert: baseAlert,
      status: "diagnosing",
      duration_seconds: 0,
      trace: {
        steps: [
          {
            step: 1,
            timestamp: "2026-04-08T10:00:00.000Z",
            thought: "Call metrics",
            action_type: "tool_call",
            tool_name: "query_metrics",
            tool_params: { service: "auth-svc" },
          },
        ],
      },
      diagnosis_result: {
        root_cause: "GPU \u4e89\u7528",
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03", "gpu:0"],
        confidence: 0.91,
        hypotheses: [],
        impact_summary: "impact",
        affected_services: ["inference-gateway"],
        triage_priority: "P1",
        diagnosis_certainty: "confirmed",
      },
    };

    const view = buildDiagnosisLiveView(session, [
      {
        id: "assistant-latest",
        role: "assistant",
        content: "latest answer",
        created_at: "2026-04-08T11:32:06.000Z",
      },
    ]);

    const expected = formatDateTimeParts("2026-04-08T11:32:06.000Z");

    expect(view.summary?.updatedTimeLabel).toBe(expected.time);
    expect(view.summary?.updatedDateTimeLabel).toBe(expected.date + " " + expected.time);
  });

  it("uses localized certainty labels and populates demo summary timestamps", () => {
    const scenario = buildDiagnosisDemoScenario("Analyze inference-gateway latency spike");

    expect(scenario.summary.certaintyLabel).toBe("\u5df2\u786e\u8ba4");
    expect(scenario.summary.updatedTimeLabel).not.toBe("--");
    expect(scenario.summary.updatedDateTimeLabel).not.toBe("--");
    expect(scenario.summary.title).toBe("\u6839\u56e0\u8bca\u65ad");
  });
});


describe("diagnosis remediation audit timeline", () => {
  it("builds a single approval result plus execution progress system events", () => {
    const session: DiagnosisSession = {
      session_id: "sess-audit-1",
      alert: baseAlert,
      status: "remediating",
      duration_seconds: 0,
      diagnosis_result: {
        root_cause: "GPU contention",
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03"],
        confidence: 0.82,
        hypotheses: [],
        impact_summary: "impact",
        affected_services: ["auth-svc"],
        triage_priority: "P1",
        diagnosis_certainty: "probable",
        recommended_fix: {
          plan_id: "plan-gpu-v3",
          root_cause: "GPU contention",
          description: "Drain canary shards first",
          steps: [
            {
              step_id: 1,
              description: "Drain worker-03 canary",
              tool: "kubectl",
              params: { node: "worker-03" },
              verification: { method: "wait", wait_seconds: 60 },
              timeout: 120,
            },
          ],
          estimated_impact: "low",
          confidence: 0.82,
          priority: "P1",
        },
      },
    };

    const localAuditRecords = [
      {
        id: "local-approval-1",
        sessionId: "sess-audit-1",
        eventKind: "approval_result" as const,
        source: "optimistic" as const,
        dedupeKey: "approval-result-approved-v3",
        timestamp: "2026-04-08T11:00:00.000Z",
        summary: "[\u7cfb\u7edf] \u5df2\u5b8c\u6210\u6267\u884c\u786e\u8ba4\uff08v3\uff0c\u5ba1\u6279\u4eba alice\uff09",
        details: ["\u5ba1\u6279\u65f6\u95f4\uff1a2026/04/08 19:00:00"],
        statusTone: "success" as const,
      },
    ];

    const events = [
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-audit-1",
        timestamp: "2026-04-08T11:00:05.000Z",
        data: {
          stage: "execution_started",
          approver: "alice",
          plan_version: 3,
          message: "\u5f00\u59cb\u6267\u884c\u6b65\u9aa4 1",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-audit-1",
        timestamp: "2026-04-08T11:00:20.000Z",
        data: {
          stage: "execution_succeeded",
          user: "system",
          message: "\u91d1\u4e1d\u96c0\u6279\u6b21\u9a8c\u8bc1\u901a\u8fc7",
        },
      },
    ];

    const view = buildDiagnosisLiveView(session, [], events, localAuditRecords);
    const systemItems = view.timeline.filter((item) => item.kind === "system");
    const runItems = view.timeline.filter((item) => item.kind === "run");

    expect(runItems).toHaveLength(2);
    expect(systemItems).toHaveLength(1);
    const approvalItems = systemItems.filter(
      (item) => item.kind === "system" && item.eventKind === "approval_result",
    );
    expect(approvalItems).toHaveLength(1);
    expect(approvalItems[0]?.summary).toContain("\u5ba1\u6279\u4eba alice");

    const allRunSummaries = runItems.flatMap((item) => item.steps.map((step) => step.summary));
    expect(allRunSummaries).toEqual(
      expect.arrayContaining([
        "[\u7cfb\u7edf] \u5f00\u59cb\u6267\u884c\uff1a\u5f00\u59cb\u6267\u884c\u6b65\u9aa4 1",
        "[\u7cfb\u7edf] \u6267\u884c\u6210\u529f\uff1a\u91d1\u4e1d\u96c0\u6279\u6b21\u9a8c\u8bc1\u901a\u8fc7",
      ]),
    );
    expect(runItems.some((item) => item.phase === "canary")).toBe(true);
    expect(runItems.some((item) => item.phase === "full")).toBe(true);
  });

  it("splits canary and full rollout into separate run blocks and keeps metric feedback as a system event", () => {
    const session: DiagnosisSession = {
      session_id: "sess-canary-1",
      alert: baseAlert,
      status: "validating",
      duration_seconds: 0,
      diagnosis_result: {
        root_cause: "GPU contention",
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03"],
        confidence: 0.9,
        hypotheses: [],
        impact_summary: "impact",
        affected_services: ["vllm-serving"],
        triage_priority: "P1",
        diagnosis_certainty: "confirmed",
        recommended_fix: {
          plan_id: "plan-gpu-v1",
          root_cause: "GPU contention",
          description: "Canary first",
          steps: [
            {
              step_id: 1,
              description: "Shift canary traffic",
              tool: "traffic_shift",
              params: { node: "worker-03" },
              verification: { method: "wait", wait_seconds: 60 },
              timeout: 120,
            },
          ],
          canary: {
            enabled: true,
            target_percentage: 10,
            monitor_duration: 15,
            success_criteria: [{ metric: "vllm_p95_ms", operator: "<=", value: 1800 }],
          },
          estimated_impact: "low",
          confidence: 0.9,
          priority: "P1",
        },
      },
    };

    const events = [
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-canary-1",
        timestamp: "2026-04-08T11:01:00.000Z",
        data: { stage: "canary_started", skill_id: "builtin-vllm-diagnosis", progress: 10 },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-canary-1",
        timestamp: "2026-04-08T11:02:00.000Z",
        data: { stage: "canary_succeeded" },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-canary-1",
        timestamp: "2026-04-08T11:03:00.000Z",
        data: { stage: "observation_result", metrics_improved: true, alert_cleared: true },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-canary-1",
        timestamp: "2026-04-08T11:04:00.000Z",
        data: { stage: "full_rollout_started", progress: 35 },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-canary-1",
        timestamp: "2026-04-08T11:05:00.000Z",
        data: { stage: "alert_recovered" },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-canary-1",
        timestamp: "2026-04-08T11:06:00.000Z",
        data: { stage: "session_closed" },
      },
    ];

    const view = buildDiagnosisLiveView(session, [], events, []);
    const runItems = view.timeline.filter((item) => item.kind === "run");
    const metricFeedbackItems = view.timeline.filter(
      (item) => item.kind === "system" && item.eventKind === "metric_feedback",
    );
    const recoveryItems = view.timeline.filter(
      (item) => item.kind === "system" && item.eventKind === "alert_recovery",
    );
    const closeItems = view.timeline.filter(
      (item) => item.kind === "system" && item.eventKind === "session_closed",
    );

    expect(runItems).toHaveLength(2);
    expect(runItems.find((item) => item.phase === "canary")?.steps).toHaveLength(2);
    expect(runItems.find((item) => item.phase === "full")?.steps.map((item) => item.summary)).toEqual(
      expect.arrayContaining([
        "[\u7cfb\u7edf] \u5f00\u59cb\u5168\u91cf\u4fee\u590d\uff1a\u5f00\u59cb\u5168\u91cf\u4fee\u590d",
      ]),
    );
    expect(metricFeedbackItems).toHaveLength(1);
    expect(recoveryItems).toHaveLength(1);
    expect(closeItems).toHaveLength(1);
  });

  it("groups execution stages by explicit run identifiers before falling back to session order", () => {
    const session = createSession([]);
    const events = [
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-live-1",
        timestamp: "2026-04-08T11:01:00.000Z",
        data: { stage: "canary_started", rollout_id: "rollout-a", progress: 10 },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-live-1",
        timestamp: "2026-04-08T11:02:00.000Z",
        data: { stage: "canary_progress", rollout_id: "rollout-b", progress: 30 },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-live-1",
        timestamp: "2026-04-08T11:03:00.000Z",
        data: { stage: "canary_succeeded", rollout_id: "rollout-a", progress: 100 },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-live-1",
        timestamp: "2026-04-08T11:04:00.000Z",
        data: { stage: "full_rollout_started", progress: 35 },
      },
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-live-1",
        timestamp: "2026-04-08T11:05:00.000Z",
        data: { stage: "full_rollout_succeeded", progress: 100 },
      },
    ];

    const view = buildDiagnosisLiveView(session, [], events, []);
    const runItems = view.timeline.filter((item) => item.kind === "run");

    expect(runItems.map((item) => item.runId)).toEqual([
      "rollout-a",
      "rollout-b",
      "sess-live-1-execution-run",
    ]);
    expect(runItems.find((item) => item.runId === "rollout-a")?.steps).toHaveLength(2);
    expect(runItems.find((item) => item.runId === "sess-live-1-execution-run")?.steps).toHaveLength(2);
  });

  it("keeps normal chat, diagnostic tools, and reports outside execution run blocks", () => {
    const session: DiagnosisSession = {
      ...createSession([
        {
          step: 1,
          timestamp: "2026-04-08T10:59:00.000Z",
          thought: "Check metrics before remediation",
          action_type: "tool_call",
          tool_name: "query_metrics",
          tool_params: { service: "auth-svc" },
        },
        {
          tool: "query_metrics",
          params: { service: "auth-svc" },
          result: { p95: "5.2s" },
          timestamp: "2026-04-08T10:59:10.000Z",
        },
      ]),
      diagnosis_result: {
        root_cause: "GPU contention",
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03"],
        confidence: 0.9,
        hypotheses: [],
        impact_summary: "impact",
        affected_services: ["auth-svc"],
        triage_priority: "P1",
        diagnosis_certainty: "confirmed",
      },
    };
    const events = [
      {
        schema_version: "1",
        type: "remediation_progress" as const,
        session_id: "sess-live-1",
        timestamp: "2026-04-08T11:01:00.000Z",
        data: { stage: "canary_started", progress: 10 },
      },
    ];

    const view = buildDiagnosisLiveView(
      session,
      [
        {
          id: "user-chat-1",
          role: "user",
          content: "explain impact",
          created_at: "2026-04-08T11:02:00.000Z",
        },
      ],
      events,
      [],
    );

    expect(view.timeline.some((item) => item.kind === "message")).toBe(true);
    expect(view.timeline.some((item) => item.kind === "tool")).toBe(true);
    expect(view.timeline.some((item) => item.kind === "report")).toBe(true);
    expect(view.timeline.filter((item) => item.kind === "run")).toHaveLength(1);
  });

});
