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
  it("prefers backend-provided next_action and thought_duration_sec fields", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:35:01.000Z",
        thought: "Inspect queue depth before concluding",
        action_type: "conclude",
        next_action: "Next action: use backend supplied narration.",
        thought_duration_sec: 12,
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);
    const thinking = view.timeline[0];
    const nextAction = view.timeline[1];

    expect(thinking?.kind).toBe("thinking");
    if (thinking?.kind === "thinking") {
      expect(thinking.thoughtDurationSec).toBe(12);
    }
    expect(nextAction?.kind).toBe("message");
    if (nextAction?.kind === "message") {
      expect(nextAction.content).toBe("Next action: use backend supplied narration.");
    }
  });

  it("injects a next-action assistant message right after tool-call thinking", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:40:01.000Z",
        thought: "Call metrics before deciding",
        action_type: "tool_call",
        tool_name: "query_metrics",
        tool_params: { service: "auth-svc" },
        next_action: "Next action: call query_metrics for auth-svc and compare p95 with baseline.",
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
    expect(view.timeline[1]?.kind).toBe("message");
    expect(view.timeline[2]?.kind).toBe("tool");

    const nextAction = view.timeline[1];
    if (nextAction?.kind === "message") {
      expect(nextAction.role).toBe("assistant");
      expect(nextAction.label).toBe("Next action");
      expect(nextAction.content).toBe("Next action: call query_metrics for auth-svc and compare p95 with baseline.");
    }
  });

  it("does not inject next-action narration when backend next_action is missing", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:50:01.000Z",
        thought: "Evidence is sufficient to conclude",
        action_type: "conclude",
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);

    expect(view.timeline).toHaveLength(1);
    expect(view.timeline[0]?.kind).toBe("thinking");
    expect(view.timeline[0]?.kind).not.toBe("message");
  });
});

describe("buildDiagnosisLiveView live thinking merge", () => {
  it("builds deterministic live ids from thought_key+timestamp when round_id is absent", () => {
    const view = buildDiagnosisLiveView(
      createSession([]),
      [],
      [],
      [],
      {
        thought_key: "run-reason-1:reason",
        node: "reason",
        run_id: "run-reason-1",
        timestamp: "2026-04-08T11:00:01.000Z",
        content: "Streaming reasoning",
        status: "thinking",
        tool_name: "query_metrics",
        active_tools: [{ tool: "query_metrics", params: { service: "auth-svc" } }],
      },
    );

    expect(view.timeline[0]).toMatchObject({
      id: "trace-thinking-run-reason-1:reason-2026-04-08T11:00:01.000Z",
      kind: "thinking",
      status: "thinking",
      content: "Streaming reasoning",
    });
    expect(view.timeline[1]).toMatchObject({
      id: "trace-tool-run-reason-1:reason-2026-04-08T11:00:01.000Z-query_metrics-1",
      kind: "tool",
      status: "loading",
      toolName: "query_metrics",
    });
  });

  it("prefers round_id for live thinking/tool ids", () => {
    const view = buildDiagnosisLiveView(
      createSession([]),
      [],
      [],
      [],
      {
        round_id: "round-reason-2",
        thought_key: "run-reason-1:reason",
        node: "reason",
        run_id: "run-reason-1",
        timestamp: "2026-04-08T11:00:05.000Z",
        content: "Streaming reasoning round 2",
        status: "thinking",
        tool_name: "query_metrics",
        active_tools: [{ tool: "query_metrics", params: { service: "auth-svc" }, round_id: "round-reason-2" }],
      },
    );

    expect(view.timeline[0]).toMatchObject({
      id: "trace-thinking-round-reason-2",
      kind: "thinking",
      status: "thinking",
    });
    expect(view.timeline[1]).toMatchObject({
      id: "trace-tool-round-reason-2-query_metrics-1",
      kind: "tool",
      status: "loading",
    });
  });

  it("places live final answer content after the active thinking/tool stream", () => {
    const view = buildDiagnosisLiveView(
      createSession([]),
      [],
      [],
      [],
      {
        thought_key: "run-reason-1:reason",
        node: "reason",
        run_id: "run-reason-1",
        timestamp: "2026-04-08T11:00:01.000Z",
        content: "Streaming reasoning",
        status: "thinking",
        tool_name: "query_metrics",
        active_tools: [{ tool: "query_metrics", params: { service: "auth-svc" } }],
      },
      {
        id: "live-final-sess-1",
        timestamp: "2026-04-08T11:00:04.000Z",
        content: "诊断结论：Node contention。",
        status: "streaming",
      },
    );

    expect(view.timeline.map((item) => item.kind)).toEqual(["thinking", "tool", "message"]);
    const finalMessage = view.timeline[2];
    expect(finalMessage).toMatchObject({
      id: "live-final-sess-1",
      kind: "message",
      label: "诊断结论生成中",
      content: "诊断结论：Node contention。",
    });
  });
});

describe("buildDiagnosisLiveView trace thinking ids", () => {
  it("keeps multiple thinking items when trace reuses the same thought_key across rounds", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T11:10:01.000Z",
        thought: "round 1",
        action_type: "conclude",
        thought_key: "run-reason-dup:reason",
      },
      {
        step: 2,
        timestamp: "2026-04-08T11:10:03.000Z",
        thought: "round 2",
        action_type: "conclude",
        thought_key: "run-reason-dup:reason",
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);
    const thinkingItems = view.timeline.filter(
      (item): item is Extract<DiagnosisTimelineItem, { kind: "thinking" }> => item.kind === "thinking",
    );

    expect(thinkingItems).toHaveLength(2);
    expect(thinkingItems[0]?.id).not.toBe(thinkingItems[1]?.id);
    expect(thinkingItems[0]?.content).toBe("round 1");
    expect(thinkingItems[1]?.content).toBe("round 2");
  });
});

describe("buildDiagnosisLiveView summary timing", () => {
  it("does not create an RCA summary before a diagnosis result exists", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:55:01.000Z",
        thought: "Gathering evidence before making a conclusion",
        action_type: "tool_call",
        tool_name: "query_metrics",
        tool_params: { service: "auth-svc" },
      },
    ]);

    const view = buildDiagnosisLiveView(session, []);

    expect(view.summary).toBeUndefined();
    expect(view.candidates).toEqual([]);
    expect(view.hypotheses).toEqual([]);
    expect(view.propagationChain).toEqual([]);
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

describe("diagnosis plan extraction", () => {
  it("falls back to the top-ranked candidate recommended_fix when diagnosis_result.recommended_fix is missing", () => {
    const session: DiagnosisSession = {
      session_id: "sess-plan-fallback",
      alert: baseAlert,
      status: "approval_required",
      duration_seconds: 0,
      diagnosis_result: {
        root_cause: "GPU contention",
        root_cause_layer: "platform",
        root_cause_entities: ["node:worker-03"],
        confidence: 0.78,
        hypotheses: [],
        impact_summary: "impact",
        affected_services: ["auth-svc"],
        triage_priority: "P2",
        diagnosis_certainty: "probable",
        ranked_candidates: [
          {
            rank: 2,
            root_cause: "Secondary candidate",
            root_cause_layer: "service",
            root_cause_entities: [],
            confidence: 0.61,
            evidence_summary: "secondary",
          },
          {
            rank: 1,
            root_cause: "Primary candidate",
            root_cause_layer: "platform",
            root_cause_entities: ["node:worker-03"],
            confidence: 0.78,
            evidence_summary: "primary",
            recommended_fix: {
              plan_id: "plan-primary-v1",
              root_cause: "Primary candidate",
              description: "Drain worker-03",
              steps: [
                {
                  step_id: 1,
                  description: "Drain canary",
                  tool: "kubectl",
                  params: { node: "worker-03" },
                  verification: { method: "wait", wait_seconds: 60 },
                  timeout: 120,
                },
              ],
              estimated_impact: "low",
              confidence: 0.78,
              priority: "P2",
            },
          },
        ],
      },
    };

    const view = buildDiagnosisLiveView(session, []);

    expect(view.plan).toMatchObject({
      title: "Primary candidate",
      description: "Drain worker-03",
      priorityLabel: "P2",
    });
    expect(view.plan?.steps).toHaveLength(1);
    expect(view.plan?.steps[0]).toMatchObject({
      title: "Drain canary",
      toolName: "kubectl",
    });
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
        summary: "[系统] 已审批，通过执行（v3，审批人 alice）",
        details: ["审批时间：2026/04/08 19:00:00"],
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
          message: "开始执行步骤 1",
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
          message: "金丝雀批次验证通过",
        },
      },
    ];

    const view = buildDiagnosisLiveView(session, [], events, localAuditRecords);
    const systemItems = view.timeline.filter((item) => item.kind === "system");

    expect(systemItems).toHaveLength(3);
    const approvalItems = systemItems.filter(
      (item) => item.kind === "system" && item.eventKind === "approval_result",
    );
    expect(approvalItems).toHaveLength(1);
    expect(approvalItems[0]?.summary).toContain("审批人 alice");
    expect(systemItems.map((item) => item.summary)).toEqual(
      expect.arrayContaining([
        "[系统] 开始执行：开始执行步骤 1",
        "[系统] 执行成功：金丝雀批次验证通过",
      ]),
    );
  });
});
