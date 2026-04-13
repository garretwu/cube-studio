import { describe, expect, it } from "vitest";

import type { Alert, DiagnosisSession } from "../api/types";
import {
  buildDiagnosisModifiedDemoScenario,
  buildDiagnosisModifiedLiveView,
  type DiagnosisModifiedTimelineItem,
} from "./diagnosisModifiedModel";

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

describe("buildDiagnosisModifiedLiveView tool matching", () => {
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

    const view = buildDiagnosisModifiedLiveView(session, []);
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

    const view = buildDiagnosisModifiedLiveView(session, []);
    const deploymentTools = view.timeline
      .filter((item): item is Extract<DiagnosisModifiedTimelineItem, { kind: "tool" }> => item.kind === "tool")
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

    const view = buildDiagnosisModifiedLiveView(session, []);
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

    const view = buildDiagnosisModifiedLiveView(session, []);
    const toolItems = view.timeline.filter((item) => item.kind === "tool");

    expect(toolItems).toHaveLength(1);
    expect(toolItems[0]?.status).toBe("loading");
    expect(toolItems[0]?.summaryLines).toEqual(["等待 tool_result 返回..."]);
  });
});

describe("buildDiagnosisModifiedLiveView next-action narration", () => {
  it("prefers backend-provided next_action and thought_duration_sec fields", () => {
    const session = createSession([
      {
        step: 1,
        timestamp: "2026-04-08T10:40:01.000Z",
        thought: "Inspect queue depth before concluding",
        action_type: "conclude",
        next_action: "下一步：使用后端返回的行动建议。",
        thought_duration_sec: 9,
      },
    ]);

    const view = buildDiagnosisModifiedLiveView(session, []);
    const thinking = view.timeline[0];
    const nextAction = view.timeline[1];

    expect(thinking?.kind).toBe("thinking");
    if (thinking?.kind === "thinking") {
      expect(thinking.thoughtDurationSec).toBe(9);
    }
    expect(nextAction?.kind).toBe("message");
    if (nextAction?.kind === "message") {
      expect(nextAction.role).toBe("assistant");
      expect(nextAction.label).toBe("下一步行动");
      expect(nextAction.content).toBe("下一步：使用后端返回的行动建议。");
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

    const view = buildDiagnosisModifiedLiveView(session, []);

    expect(view.timeline).toHaveLength(1);
    expect(view.timeline[0]?.kind).toBe("thinking");
    expect(view.timeline[0]?.kind).not.toBe("message");
  });
});
describe("buildDiagnosisModifiedDemoScenario ReAct cadence", () => {
  it("ensures every thinking append is followed by an assistant conclusion append", () => {
    const scenario = buildDiagnosisModifiedDemoScenario("Analyze auth-svc latency and error-rate spike");
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
