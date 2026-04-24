import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisLocalAuditRecord, DiagnosisSession } from "../api/types";
import { useDiagnosisStore } from "../store/diagnosisStore";
import DiagnosisPage from "./Diagnosis";
import * as diagnosisModel from "./diagnosisModel";
import type { DiagnosisCandidateView, DiagnosisDemoScenario, DiagnosisHypothesisView, DiagnosisPropagationStepView, DiagnosisTimelineItem } from "./diagnosisModel";

vi.mock("../hooks/useWebSocket", () => ({
  useWebSocket: () => ({ state: "closed" as const }),
}));

vi.mock("./diagnosisModel", async () => {
  const actual = await vi.importActual<typeof import("./diagnosisModel")>("./diagnosisModel");
  return {
    ...actual,
    buildDiagnosisDemoScenario: vi.fn(actual.buildDiagnosisDemoScenario),
    buildDiagnosisLiveView: vi.fn(actual.buildDiagnosisLiveView),
  };
});

const baseSummary: DiagnosisDemoScenario["summary"] = {
  title: "\u6839\u56e0\u8bca\u65ad",
  subtitle: "Demo subtitle",
  certaintyLabel: "Likely",
  certaintyTone: "accent",
  confidenceLabel: "80%",
  affectedServices: ["auth-svc"],
  impactSummary: "Demo impact",
};

const basePlan: DiagnosisDemoScenario["plan"] = {
  title: "Demo plan",
  description: "Demo remediation plan",
  priorityLabel: "P1",
  confidenceLabel: "80%",
  steps: [],
};

function createReportItem(
  overrides: Partial<Extract<DiagnosisTimelineItem, { kind: "report" }>> = {},
): Extract<DiagnosisTimelineItem, { kind: "report" }> {
  return {
    id: overrides.id ?? "report-1",
    kind: "report",
    timestamp: overrides.timestamp ?? "2026-04-08T10:25:02.000Z",
    summary: overrides.summary ?? baseSummary,
    candidates: overrides.candidates ?? [],
    hypotheses: overrides.hypotheses ?? [],
    propagationChain: overrides.propagationChain ?? [],
    planStatusLabel: overrides.planStatusLabel,
    planStatusTone: overrides.planStatusTone ?? "info",
  };
}

function createRunItem(
  overrides: Partial<Extract<DiagnosisTimelineItem, { kind: "run" }>> = {},
): Extract<DiagnosisTimelineItem, { kind: "run" }> {
  return {
    id: overrides.id ?? "run-sess-1",
    kind: "run",
    runId: overrides.runId ?? "sess-live-execution-run",
    phase: overrides.phase ?? "canary",
    title: overrides.title ?? "\u7070\u5ea6\u6267\u884c\u8fd0\u884c\u5757",
    timestamp: overrides.timestamp ?? (overrides.updatedAt ?? "2026-04-08T11:02:00.000Z"),
    status: overrides.status ?? "running",
    progress: overrides.progress ?? { label: "\u7070\u5ea6\u8fdb\u5ea6", value: 45 },
    currentStageLabel: overrides.currentStageLabel ?? "\u7070\u5ea6\u6267\u884c\u4e2d",
    startedAt: overrides.startedAt ?? "2026-04-08T11:00:00.000Z",
    updatedAt: overrides.updatedAt ?? "2026-04-08T11:02:00.000Z",
    metrics: overrides.metrics ?? ["\u5f53\u524d p95\uff1a1.72s", "\u9519\u8bef\u7387\uff1a0.4%"],
    tools: overrides.tools ?? [],
    steps: overrides.steps ?? [
      {
        id: "run-step-1",
        eventKind: "canary_progress",
        title: "\u7070\u5ea6\u6267\u884c\u4e2d",
        summary: "[\u7cfb\u7edf] \u7070\u5ea6\u6267\u884c\u4e2d\uff1a\u9996\u6279\u5b9e\u4f8b\u5df2\u5b8c\u6210\u5207\u6362",
        details: ["\u7070\u5ea6\u8fdb\u5ea6\uff1a45%", "\u5f53\u524d p95\uff1a1.72s"],
        timestamp: "2026-04-08T11:02:00.000Z",
        status: "running",
        statusTone: "warning",
        progress: { label: "\u7070\u5ea6\u8fdb\u5ea6", value: 45 },
        metricLines: ["\u5f53\u524d p95\uff1a1.72s"],
        toolIds: [],
      },
    ],
  };
}

function createLiveSession(sessionId: string): DiagnosisSession {
  return {
    session_id: sessionId,
    alert: {
      alert_name: "Latency spike",
      severity: "critical",
      labels: { service: "auth-svc" },
      annotations: { summary: "p95 spike" },
      starts_at: "2026-04-08T10:00:00.000Z",
      fingerprint: `${sessionId}-fp`,
      status: "firing",
      source: "alertmanager",
    },
    status: "diagnosing",
    duration_seconds: 0,
  };
}

/**
 * Build an approval-ready session fixture for diagnosis page tests.
 * Input: session id; Output: diagnosis session using the formal multi-root-cause contract.
 * Why: test cases must validate that UI behavior only depends on `root_cause[]`, not legacy fields.
 */
function createApprovalSession(sessionId: string): DiagnosisSession {
  return {
    ...createLiveSession(sessionId),
    status: "approval_required",
    diagnosis_result: {
      root_cause: [
        {
          id: "rc-gpu-contention",
          title: "GPU contention",
          layer: "platform",
          entities: ["node:worker-03"],
          confidence: 0.82,
          certainty: "probable",
          status: "confirmed",
          evidence_summary: "GPU utilization and queue latency rose together.",
          impact_summary: "impact",
          distinguishing_verification: "Drain canary on worker-03 and validate p95 recovery.",
        },
      ],
      confidence: 0.82,
      hypotheses: [],
      impact_summary: "impact",
      affected_services: ["auth-svc"],
      triage_priority: "P1",
      diagnosis_certainty: "probable",
      recommended_fix: {
        plan_id: "plan-gpu-v3",
        root_cause: "GPU contention",
        description: "Drain canary shards before broadening the rollout.",
        steps: [
          {
            step_id: 1,
            description: "Drain the hot node canary first",
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
}

function resetDiagnosisStore(overrides: Partial<ReturnType<typeof useDiagnosisStore.getState>> = {}) {
  const current = useDiagnosisStore.getState();
  useDiagnosisStore.setState({
    ...current,
    session: undefined,
    activeSessionId: undefined,
    messages: [],
    events: [],
    localAuditRecords: [],
    chatContextApplied: false,
    chatContextMeta: undefined,
    isLoadingSession: false,
    bootstrapStatus: "idle",
    traceStatus: "unknown",
    isSendingMessage: false,
    connectionState: "closed",
    error: undefined,
    isRevisingPlan: false,
    isApprovingPlan: false,
    approvalOverlayOpen: false,
    currentPlanVersion: null,
    latestPlanVersion: null,
    approvedPlanVersion: null,
    canApprove: false,
    approvalBlockReason: undefined,
    hasPlan: false,
    planMissingReason: undefined,
    effectiveReviseInstruction: undefined,
    bootstrapSession: vi.fn().mockResolvedValue(undefined),
    sendMessage: vi.fn().mockResolvedValue(undefined),
    revisePlan: vi.fn().mockResolvedValue(undefined),
    approvePlan: vi.fn().mockResolvedValue(undefined),
    reconcileSession: vi.fn().mockResolvedValue(undefined),
    applyEvent: vi.fn(),
    setConnectionState: vi.fn(),
    ...overrides,
  });
}

function renderDemoPage() {
  return render(
    <MemoryRouter initialEntries={["/diagnosis"]}>
      <Routes>
        <Route path="/diagnosis" element={<DiagnosisPage />} />
        <Route path="/diagnosis/:sessionId" element={<DiagnosisPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderLivePage(path = "/diagnosis/sess-live") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/diagnosis/:sessionId" element={<DiagnosisPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function RemediationRouteProbe() {
  const location = useLocation();
  return <div data-testid="remediation-route-probe">{`${location.pathname}${location.search}`}</div>;
}

function renderDemoPageWithRemediation(path = "/diagnosis") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/diagnosis" element={<DiagnosisPage />} />
        <Route path="/remediation" element={<RemediationRouteProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderLivePageWithRemediation(path = "/diagnosis/sess-live") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/diagnosis/:sessionId" element={<DiagnosisPage />} />
        <Route path="/remediation" element={<RemediationRouteProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function advance(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}


async function flushPendingTimers() {
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
    await Promise.resolve();
  });
}
describe("DiagnosisPage sequential playback", () => {
  const mockedBuildDemoScenario = vi.mocked(diagnosisModel.buildDiagnosisDemoScenario);
  const mockedBuildLiveView = vi.mocked(diagnosisModel.buildDiagnosisLiveView);

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    resetDiagnosisStore();
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("uses realtime entry on /diagnosis submit and calls startStreamingDiagnosis", async () => {
    const startStreamingDiagnosis = vi.fn().mockReturnValue("pending-sess-entry");
    resetDiagnosisStore({
      startStreamingDiagnosis,
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderDemoPage();

    const input = screen.getByPlaceholderText(/Continue the current diagnosis session/i);
    fireEvent.change(input, { target: { value: "check auth latency spike" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(20);
    expect(startStreamingDiagnosis).toHaveBeenCalledTimes(1);
  });

  it("shows thinking placeholder on pending live session route", () => {
    resetDiagnosisStore({
      session: createLiveSession("sess-live-pending"),
      activeSessionId: "sess-live-pending",
      bootstrapStatus: "loading",
      traceStatus: "unknown",
      isStreamingDiagnosis: true,
      streamingPhase: "waiting_first_content",
      liveThinking: {
        thought_key: "sess-live-pending:node",
        timestamp: "2026-04-08T10:00:00.000Z",
        content: "Collecting evidence from live streams...",
        status: "thinking",
        active_tools: [],
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-pending");

    expect(screen.getByText("Thinking...")).toBeInTheDocument();
    expect(screen.getByText("Collecting evidence from live streams...")).toBeInTheDocument();
    expect(
      screen.queryByText(/Submit a request to start a realtime diagnosis session/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/The live session has not produced trace entries yet/i),
    ).not.toBeInTheDocument();
  });

  it.skip("shows the second assistant message only after the first stream completes", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-1",
          kind: "message",
          role: "user",
          content: "demo request",
          timestamp: "2026-04-08T10:00:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "assistant-1",
            kind: "message",
            role: "assistant",
            content: "first-stream",
            timestamp: "2026-04-08T10:00:01.000Z",
          },
        },
        {
          delayMs: 1,
          type: "append",
          item: {
            id: "assistant-2",
            kind: "message",
            role: "assistant",
            content: "second-stream",
            timestamp: "2026-04-08T10:00:02.000Z",
          },
        },
        {
          delayMs: 2,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    const { container } = renderDemoPage();

    const input = screen.getByPlaceholderText(/Continue the current diagnosis session/i);
    fireEvent.change(input, { target: { value: "demo request" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(120);

    expect(container.querySelectorAll(".diagnosis-workspace-message-row")).toHaveLength(2);
    expect(screen.queryByText("second-stream")).not.toBeInTheDocument();

    await flushPendingTimers();

    expect(container.querySelectorAll(".diagnosis-workspace-message-row")).toHaveLength(3);
  });

  it.skip("collapses finished thinking into a unified Thought for x seconds label", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-thinking",
          kind: "message",
          role: "user",
          content: "demo thinking",
          timestamp: "2026-04-08T10:20:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "thinking-1",
            kind: "thinking",
            title: "Agent is understanding the request",
            content: "abc",
            timestamp: "2026-04-08T10:20:01.000Z",
            status: "thinking",
          },
        },
        {
          delayMs: 1,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    renderDemoPage();

    const input = screen.getByPlaceholderText(/Continue the current diagnosis session/i);
    fireEvent.change(input, { target: { value: "demo thinking" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await flushPendingTimers();

    expect(screen.getByText(/Thought for \d+ seconds?/i)).toBeInTheDocument();
    expect(screen.queryByText("Agent is understanding the request")).not.toBeInTheDocument();
  });
  it.skip("blocks later timeline items while a demo tool is still loading", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-2",
          kind: "message",
          role: "user",
          content: "check tool sequence",
          timestamp: "2026-04-08T10:10:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "demo-tool-1",
            kind: "tool",
            toolName: "query_metrics",
            params: { service: "auth-svc" },
            timestamp: "2026-04-08T10:10:01.000Z",
            status: "loading",
            summaryLines: ["Loading metrics..."],
          },
        },
        {
          delayMs: 1,
          type: "append",
          item: {
            id: "assistant-after-tool",
            kind: "message",
            role: "assistant",
            content: "after-tool",
            timestamp: "2026-04-08T10:10:02.000Z",
          },
        },
        {
          delayMs: 200,
          type: "update_tool",
          targetId: "demo-tool-1",
          summaryLines: ["p95: 5.2s"],
        },
        {
          delayMs: 201,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    renderDemoPage();

    const input = screen.getByPlaceholderText(/Continue the current diagnosis session/i);
    fireEvent.change(input, { target: { value: "check tool sequence" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(200);

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(screen.queryByText("after-tool")).not.toBeInTheDocument();

  });

  it.skip("keeps demo tool cards in loading for at least 3.5 seconds before success", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-tool-dwell",
          kind: "message",
          role: "user",
          content: "verify minimum dwell",
          timestamp: "2026-04-08T10:15:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "demo-tool-dwell",
            kind: "tool",
            toolName: "query_metrics",
            params: { service: "auth-svc" },
            timestamp: "2026-04-08T10:15:01.000Z",
            status: "loading",
            summaryLines: ["Loading metrics..."],
          },
        },
        {
          delayMs: 1,
          type: "update_tool",
          targetId: "demo-tool-dwell",
          summaryLines: ["p95: 4.6s"],
        },
        {
          delayMs: 2,
          type: "append",
          item: {
            id: "assistant-after-dwell",
            kind: "message",
            role: "assistant",
            content: "done",
            timestamp: "2026-04-08T10:15:02.000Z",
          },
        },
        {
          delayMs: 3,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    const { container } = renderDemoPage();

    const input = screen.getByPlaceholderText(/Continue the current diagnosis session/i);
    fireEvent.change(input, { target: { value: "verify minimum dwell" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(120);

    expect(screen.getByText("loading")).toBeInTheDocument();

    await advance(3200);

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(screen.queryByText("success")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".diagnosis-workspace-message-row")).toHaveLength(1);

    await advance(1500);

    expect(screen.getByText("success")).toBeInTheDocument();

    await flushPendingTimers();

    expect(container.querySelectorAll(".diagnosis-workspace-message-row")).toHaveLength(2);
  });
  it.skip("waits for thinking completion before starting a demo tool call", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-thinking-before-tool",
          kind: "message",
          role: "user",
          content: "validate thinking then tool",
          timestamp: "2026-04-08T10:20:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "demo-thinking-before-tool",
            kind: "thinking",
            title: "Agent reasoning",
            content: "This reasoning block must complete before any tool call starts in the demo timeline.",
            timestamp: "2026-04-08T10:20:01.000Z",
            status: "thinking",
          },
        },
        {
          delayMs: 1,
          type: "append",
          item: {
            id: "demo-tool-after-thinking",
            kind: "tool",
            toolName: "query_service_metrics",
            params: { service: "auth-svc" },
            timestamp: "2026-04-08T10:20:02.000Z",
            status: "loading",
            summaryLines: ["Loading metrics..."],
          },
        },
        {
          delayMs: 1200,
          type: "update_tool",
          targetId: "demo-tool-after-thinking",
          summaryLines: ["p95: 5.6s"],
        },
        {
          delayMs: 1201,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    renderDemoPage();

    const input = screen.getByPlaceholderText(/Continue the current diagnosis session/i);
    fireEvent.change(input, { target: { value: "validate thinking then tool" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(600);

    expect(screen.queryByText("query_service_metrics")).not.toBeInTheDocument();

    await flushPendingTimers();

    expect(screen.getByText("query_service_metrics")).toBeInTheDocument();
  });

  it.skip("auto shows the demo approval card after the RCA report card", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-approval-card",
          kind: "message",
          role: "user",
          content: "show demo approval",
          timestamp: "2026-04-08T10:25:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "assistant-demo-approval",
            kind: "message",
            role: "assistant",
            content: "demo approval result",
            timestamp: "2026-04-08T10:25:01.000Z",
          },
        },
        {
          delayMs: 1,
          type: "append",
          item: createReportItem({
            id: "demo-report-approval",
            timestamp: "2026-04-08T10:25:02.000Z",
            planStatusLabel: "淇鏂规宸茬敓鎴愶紝绛夊緟瀹℃壒",
            planStatusTone: "warning",
          }),
        },
        {
          delayMs: 2,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: {
        ...basePlan,
        steps: [
          {
            id: "step-1",
            title: "Drain canary first",
            detail: "kubectl | wait 120s | verify wait",
            status: "pending",
          },
        ],
      },
    });

    const { container } = renderDemoPage();

    const input = screen.getByPlaceholderText(/Continue the current diagnosis session/i);
    fireEvent.change(input, { target: { value: "show demo approval" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    expect(screen.queryByTestId("diagnosis-demo-approval-card")).not.toBeInTheDocument();

    await advance(5000);
    await flushPendingTimers();
    await advance(500);

    const approvalCard = screen.getByTestId("diagnosis-demo-approval-card");
    const approvalLayer = container.querySelector(".diagnosis-workspace-approval-layer");
    const composerAnchor = container.querySelector(".diagnosis-workspace-composer-anchor");
    const feed = container.querySelector(".diagnosis-workspace-feed");

    expect(approvalCard).toHaveClass(
      "diagnosis-workspace-approval-surface--floating",
    );
    expect(approvalLayer).not.toBeNull();
    expect(feed).toHaveClass("diagnosis-workspace-feed--with-approval");
    expect(
      container.querySelector(".diagnosis-workspace-feed .diagnosis-workspace-approval-surface"),
    ).toBeNull();
    expect(composerAnchor).not.toHaveClass(
      "diagnosis-workspace-composer-anchor--with-approval",
    );
    expect(
      composerAnchor?.querySelector('[data-testid="diagnosis-demo-approval-card"]'),
    ).toBeNull();
    expect(
      approvalLayer?.querySelector('[data-testid="diagnosis-demo-approval-card"]'),
    ).toBe(approvalCard);
    expect(screen.queryByText("kubectl | wait 120s | verify wait")).not.toBeInTheDocument();
    expect(screen.getByText("Drain canary first")).toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-demo-approve-button")).toBeInTheDocument();
  });


  it.skip("continues the demo loop after approval through canary, rollout, recovery, and closure", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-approved-loop",
          kind: "message",
          role: "user",
          content: "run approved remediation loop",
          timestamp: "2026-04-08T10:30:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "assistant-demo-approved-loop",
            kind: "message",
            role: "assistant",
            content: "approval ready",
            timestamp: "2026-04-08T10:30:01.000Z",
          },
        },
        {
          delayMs: 1,
          type: "append",
          item: createReportItem({
            id: "demo-report-approved-loop",
            timestamp: "2026-04-08T10:30:02.000Z",
            planStatusLabel: "淇鏂规宸茬敓鎴愶紝绛夊緟瀹℃壒",
            planStatusTone: "warning",
          }),
        },
        {
          delayMs: 2,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: {
        ...basePlan,
        steps: [
          {
            id: "step-1",
            title: "Drain canary first",
            detail: "kubectl | wait 120s | verify wait",
            status: "pending",
          },
        ],
      },
    });

    const { container } = renderDemoPage();

    const input = screen.getByPlaceholderText(/Continue the current diagnosis session/i);
    fireEvent.change(input, { target: { value: "run approved remediation loop" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(5000);
    await flushPendingTimers();
    await advance(500);

    fireEvent.click(screen.getByTestId("diagnosis-demo-approve-button"));

    for (let step = 0; step < 8; step += 1) {
      await advance(10000);
      await flushPendingTimers();
    }

    expect(screen.queryByTestId("diagnosis-demo-approval-card")).not.toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-approval-result-thought")).toBeInTheDocument();
    expect(screen.getByText(/Thought for \d+ seconds?/i)).toBeInTheDocument();
    expect(screen.queryByText("[\u7cfb\u7edf] \u5df2\u7ecf\u5b8c\u6210\u6267\u884c\u786e\u8ba4")).not.toBeInTheDocument();

    const demoExecutionCard = screen.getByTestId("diagnosis-demo-execution-card");
    expect(demoExecutionCard).toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-execution-run-block")).not.toBeInTheDocument();

    const stageRows = screen.getAllByTestId("diagnosis-demo-execution-stage");
    expect(stageRows).toHaveLength(4);
    expect(stageRows.map((row) => row.getAttribute("data-stage-key"))).toEqual([
      "execution_started",
      "observation_started",
      "observation_result",
      "execution_succeeded",
    ]);

    expect(screen.getByText("\u6267\u884c\u5f00\u59cb")).toBeInTheDocument();
    expect(screen.getByText("\u89c2\u5bdf\u5f00\u59cb")).toBeInTheDocument();
    expect(screen.getByText("\u89c2\u5bdf\u7ed3\u8bba")).toBeInTheDocument();
    expect(screen.getByText("\u6267\u884c\u6210\u529f")).toBeInTheDocument();

    expect(screen.getByText(/\u8c03\u7528\u4fee\u590d\u5de5\u5177\uff1arun_skill/u)).toBeInTheDocument();
    expect(screen.getByText(/\u89c2\u5bdf\u65f6\u957f\uff1a3s/u)).toBeInTheDocument();
    expect(screen.getByText(/\u89c2\u5bdf\u7ed3\u8bba\uff1a\u901a\u8fc7/u)).toBeInTheDocument();
    expect(
      screen.getByText(/\u7ed3\u679c\uff1a\u544a\u8b66\u5df2\u6062\u590d\uff0c\u8bca\u65ad\u5df2\u5173\u95ed/u),
    ).toBeInTheDocument();

    expect(container.querySelectorAll(".diagnosis-workspace-run-block").length).toBe(0);
    expect(screen.queryByText(/vllm_p95_ms/i)).not.toBeInTheDocument();
  });
  it("renders live timeline directly without timeout pacing", async () => {
    let timelineSource: DiagnosisTimelineItem[] = [];
    mockedBuildLiveView.mockImplementation(() => ({
      timeline: timelineSource,
      candidates: [],
      summary: undefined,
      plan: undefined,
    }));

    resetDiagnosisStore({
      session: createLiveSession("sess-live-timeout"),
      activeSessionId: "sess-live-timeout",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-timeout");

    timelineSource = [
      createRunItem({
        id: "live-run-timeout",
        runId: "rollout-timeout",
        tools: [
          {
            id: "live-tool-1",
            toolName: "query_slow_backend",
            params: { service: "auth-svc" },
            timestamp: "2026-04-08T11:00:01.000Z",
            status: "loading",
            summaryLines: ["Waiting for tool result..."],
            stepId: "run-step-1",
          },
        ],
        steps: [
          {
            id: "run-step-1",
            eventKind: "canary_progress",
            title: "\u7070\u5ea6\u6267\u884c\u4e2d",
            summary: "[\u7cfb\u7edf] \u7070\u5ea6\u6267\u884c\u4e2d\uff1a\u7b49\u5f85\u6162\u67e5\u8be2\u8fd4\u56de",
            details: ["\u7070\u5ea6\u8fdb\u5ea6\uff1a45%"],
            timestamp: "2026-04-08T11:00:01.000Z",
            status: "running",
            statusTone: "warning",
            progress: { label: "\u7070\u5ea6\u8fdb\u5ea6", value: 45 },
            metricLines: [],
            toolIds: ["live-tool-1"],
          },
        ],
      }),
      {
        id: "live-msg-after-timeout",
        kind: "message",
        role: "assistant",
        content: "post-timeout",
        timestamp: "2026-04-08T11:00:02.000Z",
      },
    ];

    act(() => {
      const state = useDiagnosisStore.getState();
      useDiagnosisStore.setState({ ...state, messages: [...state.messages] });
    });

    await advance(200);

    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("post-timeout")).toBeInTheDocument();
    expect(screen.queryByText("timeout")).not.toBeInTheDocument();
  });

  it.skip("adds a remediation link to the demo execution card after the approval flow continues", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-remediation-link",
          kind: "message",
          role: "user",
          content: "run approved remediation loop",
          timestamp: "2026-04-08T10:30:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "assistant-demo-remediation-link",
            kind: "message",
            role: "assistant",
            content: "approval ready",
            timestamp: "2026-04-08T10:30:01.000Z",
          },
        },
        {
          delayMs: 1,
          type: "append",
          item: createReportItem({
            id: "demo-report-remediation-link",
            timestamp: "2026-04-08T10:30:02.000Z",
            planStatusLabel: "锟睫革拷锟斤拷锟斤拷锟斤拷锟斤拷锟缴ｏ拷锟饺达拷锟斤拷锟斤拷",
            planStatusTone: "warning",
            summary: {
              ...baseSummary,
              sessionLabel: "demo-session-route",
            },
          }),
        },
        {
          delayMs: 2,
          type: "complete",
        },
      ],
      candidates: [],
      summary: {
        ...baseSummary,
        sessionLabel: "demo-session-route",
      },
      plan: {
        ...basePlan,
        steps: [
          {
            id: "demo-plan-step-1",
            title: "Drain canary first",
            detail: "kubectl | wait 120s | verify wait",
            status: "pending",
          },
        ],
      },
    });

    renderDemoPageWithRemediation();

    const input = screen.getByPlaceholderText(/Ask the agent to diagnose an issue/i);
    fireEvent.change(input, { target: { value: "run approved remediation loop" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(5000);
    await flushPendingTimers();
    await advance(500);

    fireEvent.click(screen.getByTestId("diagnosis-demo-approve-button"));

    await advance(5000);
    await flushPendingTimers();

    const demoExecutionCard = screen.getByTestId("diagnosis-demo-execution-card");
    fireEvent.click(within(demoExecutionCard).getByRole("button", { name: "\u6253\u5f00\u4fee\u590d\u6982\u89c8" }));

    expect(screen.getByTestId("remediation-route-probe")).toHaveTextContent(
      "/remediation?sessionId=demo-session-route",
    );
  });

  it("shows approval failure as an approval error instead of a diagnosis error", async () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        createReportItem({
          id: "live-report-approval-error",
          summary: {
            ...baseSummary,
            title: "根因诊断",
            subtitle: "Live approval failure summary",
          },
          planStatusLabel: "修复方案已生成，等待审批",
          planStatusTone: "warning",
        }),
      ],
      candidates: [],
      summary: {
        ...baseSummary,
        title: "根因诊断",
        subtitle: "Live approval failure summary",
      },
      plan: {
        ...basePlan,
        steps: [
          {
            id: "step-1",
            title: "Drain canary first",
            detail: "kubectl | wait 120s | verify wait",
            status: "pending",
          },
        ],
      },
    });

    resetDiagnosisStore({
      session: createApprovalSession("sess-live-approval-error"),
      activeSessionId: "sess-live-approval-error",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      approvalOverlayOpen: true,
      latestPlanVersion: 3,
      canApprove: true,
      error: "修复执行审批失败：Request failed with status 500.",
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-approval-error");

    expect(screen.getByText("修复执行审批失败：Request failed with status 500.")).toBeInTheDocument();
    expect(screen.queryByText("诊断报错")).not.toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-report-card")).toBeInTheDocument();
  });

  it("shows a warning note for partial bootstrap degradation without blocking the live diagnosis view", async () => {
    const reportItem = createReportItem({
      id: "report-partial-bootstrap",
      summary: {
        ...baseSummary,
        title: "GPU contention on worker-03",
        impactSummary: "Diagnosis result remains available while supplemental data is still loading.",
      },
    });

    mockedBuildLiveView.mockReturnValue({
      timeline: [reportItem],
      candidates: [],
      summary: reportItem.summary,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-partial-bootstrap"),
      activeSessionId: "sess-partial-bootstrap",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      error:
        "部分补充信息加载较慢，诊断结果仍可查看：对话历史未完全加载（Request timed out while waiting for the backend. Please retry.）",
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-partial-bootstrap");

    expect(screen.getByText(/部分补充信息加载较慢，诊断结果仍可查看/u)).toBeInTheDocument();
    expect(screen.queryByText("timeout of 10000ms exceeded")).not.toBeInTheDocument();
    expect(screen.getByText("GPU contention on worker-03")).toBeInTheDocument();
  });

  it("renders history immediately in live mode and serializes only incremental events", async () => {
    let timelineSource: DiagnosisTimelineItem[] = [
      {
        id: "history-msg-1",
        kind: "message",
        role: "assistant",
        content: "history-ready",
        timestamp: "2026-04-08T11:10:00.000Z",
      },
    ];

    mockedBuildLiveView.mockImplementation(() => ({
      timeline: timelineSource,
      candidates: [],
      summary: undefined,
      plan: undefined,
    }));

    resetDiagnosisStore({
      session: createLiveSession("sess-live-history"),
      activeSessionId: "sess-live-history",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      liveThinking: {
        thought_key: "sess-live-history:reason",
        timestamp: "2026-04-08T11:10:03.000Z",
        content: "streaming-current-thought",
        status: "thinking",
        active_tools: [],
      },
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis/sess-live-history");

    expect(screen.getByText("history-ready")).toBeInTheDocument();

    timelineSource = [
      ...timelineSource,
      {
        id: "live-new-1",
        kind: "message",
        role: "assistant",
        content: "incremental-one",
        timestamp: "2026-04-08T11:10:01.000Z",
      },
      {
        id: "live-new-2",
        kind: "message",
        role: "assistant",
        content: "incremental-two",
        timestamp: "2026-04-08T11:10:02.000Z",
      },
    ];

    act(() => {
      const state = useDiagnosisStore.getState();
      useDiagnosisStore.setState({ ...state, messages: [...state.messages] });
    });

    await advance(120);

    expect(screen.getByText("history-ready")).toBeInTheDocument();
    expect(screen.getByText("streaming-current-thought")).toBeInTheDocument();
    expect(screen.getByText("incremental-one")).toBeInTheDocument();
    expect(screen.getByText("incremental-two")).toBeInTheDocument();
    expect(screen.queryByText(/Next action:/i)).not.toBeInTheDocument();
    expect(container.querySelectorAll(".diagnosis-workspace-message-row")).toHaveLength(3);
  });
});





















describe("DiagnosisPage feed auto scroll", () => {
  const mockedBuildLiveView = vi.mocked(diagnosisModel.buildDiagnosisLiveView);

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("keeps the feed pinned to the bottom when new content arrives and the user is already near the bottom", async () => {
    let timelineSource: DiagnosisTimelineItem[] = [
      {
        id: "history-msg-bottom",
        kind: "message",
        role: "assistant",
        content: "history-ready",
        timestamp: "2026-04-08T11:20:00.000Z",
      },
    ];

    mockedBuildLiveView.mockImplementation(() => ({
      timeline: timelineSource,
      candidates: [],
      summary: undefined,
      plan: undefined,
    }));

    resetDiagnosisStore({
      session: createLiveSession("sess-live-scroll"),
      activeSessionId: "sess-live-scroll",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis/sess-live-scroll");
    const feed = container.querySelector(".diagnosis-workspace-feed") as HTMLDivElement | null;

    expect(feed).not.toBeNull();

    if (!feed) {
      return;
    }

    Object.defineProperties(feed, {
      clientHeight: { configurable: true, value: 320 },
      scrollHeight: { configurable: true, get: () => 960 },
      scrollTop: { configurable: true, writable: true, value: 620 },
    });

    fireEvent.scroll(feed);
    await advance(20);

    timelineSource = [
      ...timelineSource,
      {
        id: "history-msg-bottom-next",
        kind: "message",
        role: "assistant",
        content: "latest-output",
        timestamp: "2026-04-08T11:20:01.000Z",
      },
    ];

    act(() => {
      const state = useDiagnosisStore.getState();
      useDiagnosisStore.setState({ ...state, messages: [...state.messages] });
    });

    await advance(20);

    expect(feed.scrollTop).toBe(960);
  });

  it("does not force the feed back to the bottom while the user is reading history above", async () => {
    let timelineSource: DiagnosisTimelineItem[] = [
      {
        id: "history-msg-up",
        kind: "message",
        role: "assistant",
        content: "history-ready",
        timestamp: "2026-04-08T11:30:00.000Z",
      },
    ];

    mockedBuildLiveView.mockImplementation(() => ({
      timeline: timelineSource,
      candidates: [],
      summary: undefined,
      plan: undefined,
    }));

    resetDiagnosisStore({
      session: createLiveSession("sess-live-scroll-lock"),
      activeSessionId: "sess-live-scroll-lock",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis/sess-live-scroll-lock");
    const feed = container.querySelector(".diagnosis-workspace-feed") as HTMLDivElement | null;

    expect(feed).not.toBeNull();

    if (!feed) {
      return;
    }

    Object.defineProperties(feed, {
      clientHeight: { configurable: true, value: 320 },
      scrollHeight: { configurable: true, get: () => 1200 },
      scrollTop: { configurable: true, writable: true, value: 240 },
    });

    fireEvent.scroll(feed);
    await advance(20);

    timelineSource = [
      ...timelineSource,
      {
        id: "history-msg-up-next",
        kind: "message",
        role: "assistant",
        content: "latest-output",
        timestamp: "2026-04-08T11:30:01.000Z",
      },
    ];

    act(() => {
      const state = useDiagnosisStore.getState();
      useDiagnosisStore.setState({ ...state, messages: [...state.messages] });
    });

    await advance(20);

    expect(feed.scrollTop).toBe(240);
  });
});


describe("DiagnosisPage approval overlay", () => {
  const mockedBuildLiveView = vi.mocked(diagnosisModel.buildDiagnosisLiveView);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the approval overlay above the composer for approval_required sessions", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        createReportItem({
          id: "live-report-approval",
          timestamp: "2026-04-08T11:59:59.000Z",
          summary: {
            ...baseSummary,
            title: "鏍瑰洜璇婃柇",
            subtitle: "Live approval summary",
          },
          planStatusLabel: "淇鏂规宸茬敓鎴愶紝绛夊緟瀹℃壒",
          planStatusTone: "warning",
        }),
      ],
      candidates: [],
      summary: {
        ...baseSummary,
        title: "鏍瑰洜璇婃柇",
        subtitle: "Live approval summary",
      },
      plan: {
        ...basePlan,
        steps: [
          {
            id: "step-1",
            title: "Drain canary first",
            detail: "kubectl | wait 120s | verify wait",
            status: "pending",
          },
        ],
      },
    });

    resetDiagnosisStore({
      session: createApprovalSession("sess-live-approval"),
      activeSessionId: "sess-live-approval",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      approvalOverlayOpen: true,
      latestPlanVersion: 3,
      canApprove: true,
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis/sess-live-approval");

    expect(screen.getByTestId("diagnosis-approval-overlay")).toBeInTheDocument();
    expect(container.querySelector(".diagnosis-workspace-approval-layer")).not.toBeNull();
    expect(container.querySelector(".diagnosis-workspace-feed--with-approval")).not.toBeNull();
    expect(container.querySelector(".diagnosis-workspace-approval-card")).toBeNull();
    expect(
      container.querySelector(".diagnosis-workspace-composer-anchor--with-approval"),
    ).toBeNull();
  });

  it("renders a stable remediation CTA in the live session status bar and navigates to remediation", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        createReportItem({
          id: "live-report-remediation-link",
          timestamp: "2026-04-08T11:59:59.000Z",
          summary: {
            ...baseSummary,
            title: "Root cause diagnosis",
            subtitle: "Live approval summary",
          },
          planStatusLabel: "Ready for approval",
          planStatusTone: "warning",
        }),
      ],
      candidates: [],
      summary: {
        ...baseSummary,
        title: "Root cause diagnosis",
        subtitle: "Live approval summary",
        sessionLabel: "sess-live-approval",
      },
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createApprovalSession("sess-live-approval"),
      activeSessionId: "sess-live-approval",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePageWithRemediation("/diagnosis/sess-live-approval");

    fireEvent.click(screen.getByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" }));

    expect(screen.getByTestId("remediation-route-probe")).toHaveTextContent(
      "/remediation?sessionId=sess-live-approval",
    );
  });

  it("renders approval results as a lightweight thought row with filtered details", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        createReportItem({
          id: "live-report-approval-thought",
          timestamp: "2026-04-08T11:59:59.000Z",
          summary: {
            ...baseSummary,
            title: "Root cause diagnosis",
            subtitle: "Live approval summary",
          },
          planStatusLabel: "Ready for approval",
          planStatusTone: "warning",
        }),
        {
          id: "live-approval-result-thought",
          kind: "system",
          eventKind: "approval_result",
          summary: "[system] approval confirmed (v3, approver alice)",
          details: [
            "approval time: 2026/04/08 20:00:00",
            "approval feedback: execution confirmed",
            "execution boundary: canary first",
            "plan title: Drain canary first",
            "extra detail: hidden",
          ],
          timestamp: "2026-04-08T12:00:00.000Z",
          statusTone: "success",
          source: "local_audit",
          dedupeKey: "approval-result-approved-v3",
        },
      ],
      candidates: [],
      summary: {
        ...baseSummary,
        title: "Root cause diagnosis",
        subtitle: "Live approval summary",
      },
      plan: {
        ...basePlan,
        steps: [
          {
            id: "step-1",
            title: "Drain canary first",
            detail: "kubectl | wait 120s | verify wait",
            status: "pending",
          },
        ],
      },
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-approval-result"),
      activeSessionId: "sess-live-approval-result",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis/sess-live-approval-result");

    const approvalThought = screen.getByTestId("diagnosis-approval-result-thought");
    expect(approvalThought).toBeInTheDocument();
    expect(screen.getByText(/Thought for \d+ seconds?/i)).toBeInTheDocument();
    expect(screen.queryByText(/approval confirmed/i)).not.toBeInTheDocument();
    expect(container.querySelector(".diagnosis-workspace-system-event__toggle")).toBeNull();

    fireEvent.click(approvalThought);

    expect(screen.getByText("Approver: alice")).toBeInTheDocument();
    expect(screen.getByText("approval feedback: execution confirmed")).toBeInTheDocument();
    expect(screen.getByText("execution boundary: canary first")).toBeInTheDocument();
    expect(screen.getByText("plan title: Drain canary first")).toBeInTheDocument();
    expect(screen.queryByText(/approval time:/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/extra detail:/i)).not.toBeInTheDocument();
  });

  it("adds a remediation link to approval-result thought rows", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        createReportItem({
          id: "live-report-approval-thought-link",
          timestamp: "2026-04-08T11:59:59.000Z",
          summary: {
            ...baseSummary,
            title: "Root cause diagnosis",
            subtitle: "Live approval summary",
          },
          planStatusLabel: "Ready for approval",
          planStatusTone: "warning",
        }),
        {
          id: "live-approval-result-link",
          kind: "system",
          eventKind: "approval_result",
          summary: "[system] approval confirmed (v3, approver alice)",
          details: ["approval feedback: execution confirmed"],
          timestamp: "2026-04-08T12:00:00.000Z",
          statusTone: "success",
          source: "event",
          dedupeKey: "approval-result-approved-v3-link",
        },
      ],
      candidates: [],
      summary: {
        ...baseSummary,
        title: "Root cause diagnosis",
        subtitle: "Live approval summary",
        sessionLabel: "sess-live-approval-result",
      },
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-approval-result"),
      activeSessionId: "sess-live-approval-result",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePageWithRemediation("/diagnosis/sess-live-approval-result");

    const approvalThought = screen.getByTestId("diagnosis-approval-result-thought");
    const approvalArticle = approvalThought.closest("article");
    expect(approvalArticle).not.toBeNull();

    fireEvent.click(within(approvalArticle as HTMLElement).getByRole("button", { name: "\u6253\u5f00\u4fee\u590d\u6982\u89c8" }));

    expect(screen.getByTestId("remediation-route-probe")).toHaveTextContent(
      "/remediation?sessionId=sess-live-approval-result",
    );
  });

  it("adds a remediation link to execution run cards", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        createRunItem({
          id: "run-remediation-link",
          runId: "sess-live-remediating-execution-run",
          title: "Canary execution run block",
          timestamp: "2026-04-08T12:05:00.000Z",
          updatedAt: "2026-04-08T12:05:00.000Z",
        }),
      ],
      candidates: [],
      summary: {
        ...baseSummary,
        sessionLabel: "sess-live-remediating",
      },
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: { ...createLiveSession("sess-live-remediating"), status: "remediating" },
      activeSessionId: "sess-live-remediating",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePageWithRemediation("/diagnosis/sess-live-remediating");

    const runBlock = screen.getByTestId("diagnosis-execution-run-block");
    fireEvent.click(within(runBlock).getByRole("button", { name: "\u6253\u5f00\u4fee\u590d\u6982\u89c8" }));

    expect(screen.getByTestId("remediation-route-probe")).toHaveTextContent(
      "/remediation?sessionId=sess-live-remediating",
    );
  });

  it("requires a reject reason and appends a system audit item after rejection", async () => {
    mockedBuildLiveView.mockImplementation((_, __, ___, localAuditRecords = []) => ({
      timeline: [
        createReportItem({
          id: "live-report-reject",
          timestamp: "2026-04-08T11:59:59.000Z",
          summary: {
            ...baseSummary,
            title: "鏍瑰洜璇婃柇",
            subtitle: "Live approval summary",
          },
          planStatusLabel: "淇鏂规宸茬敓鎴愶紝绛夊緟瀹℃壒",
          planStatusTone: "warning",
        }),
        ...(localAuditRecords as DiagnosisLocalAuditRecord[]).map((record) => ({
          id: record.id,
          kind: "system" as const,
          eventKind: record.eventKind,
          summary: record.summary,
          details: record.details,
          timestamp: record.timestamp,
          statusTone: record.statusTone,
          source: record.source,
          dedupeKey: record.dedupeKey,
        })),
      ],
      candidates: [],
      summary: {
        ...baseSummary,
        title: "鏍瑰洜璇婃柇",
        subtitle: "Live approval summary",
      },
      plan: {
        ...basePlan,
        steps: [
          {
            id: "step-1",
            title: "Drain canary first",
            detail: "kubectl | wait 120s | verify wait",
            status: "pending",
          },
        ],
      },
    }));

    const approvePlan = vi.fn().mockImplementation(
      async ({ approved, reason }: { approved: boolean; reason?: string }) => {
        const state = useDiagnosisStore.getState();
        useDiagnosisStore.setState({
          ...state,
          approvalOverlayOpen: false,
          isApprovingPlan: false,
          session: state.session
            ? { ...state.session, status: approved ? "remediating" : "rejected" }
            : state.session,
          localAuditRecords: [
            {
              id: "audit-reject-1",
              sessionId: "sess-live-reject",
              eventKind: "approval_result",
              source: "local_audit",
              dedupeKey: "approval-result-rejected-v3",
              timestamp: "2026-04-08T12:00:00.000Z",
              summary: `[system] approval rejected (reason: ${reason})`,
              details: [
                "approval time: 2026/04/08 20:00:00",
                `reject reason: ${reason}`,
              ],
              statusTone: "danger",
            },
          ],
        });
      },
    );

    resetDiagnosisStore({
      session: createApprovalSession("sess-live-reject"),
      activeSessionId: "sess-live-reject",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      approvalOverlayOpen: true,
      latestPlanVersion: 3,
      canApprove: true,
      approvePlan,
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-reject");

    const rejectButton = screen.getByTestId("diagnosis-reject-button");
    expect(rejectButton).toBeEnabled();
    expect(screen.queryByTestId("diagnosis-approval-reason")).not.toBeInTheDocument();

    fireEvent.click(rejectButton);

    expect(screen.getByTestId("diagnosis-approval-reason")).toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-reject-button")).toBeDisabled();

    fireEvent.change(screen.getByTestId("diagnosis-approval-reason"), {
      target: { value: "\u9700\u8981\u4eba\u5de5\u590d\u6838\u53d8\u66f4\u7a97\u53e3" },
    });

    expect(screen.getByTestId("diagnosis-reject-button")).toBeEnabled();
    fireEvent.click(screen.getByTestId("diagnosis-reject-button"));

    await waitFor(() => {
      expect(approvePlan).toHaveBeenCalledWith({
        approved: false,
        reason: "\u9700\u8981\u4eba\u5de5\u590d\u6838\u53d8\u66f4\u7a97\u53e3",
      });
    });

    await waitFor(() => {
      expect(screen.queryByTestId("diagnosis-approval-overlay")).not.toBeInTheDocument();
      expect(screen.getByTestId("diagnosis-approval-result-thought")).toBeInTheDocument();
      expect(screen.getByText(/Thought for \d+ seconds?/i)).toBeInTheDocument();
      expect(screen.queryByText(/\[system\] approval rejected \(reason:/i)).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("diagnosis-approval-result-thought"));

    expect(screen.getByText(/reject reason:/i)).toBeInTheDocument();
    expect(screen.queryByText(/approval time:/i)).not.toBeInTheDocument();
  });
});


describe("DiagnosisPage status badges", () => {
  const mockedBuildLiveView = vi.mocked(diagnosisModel.buildDiagnosisLiveView);

  beforeEach(() => {
    vi.clearAllMocks();
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });
  });

  it("renders business badges for a ready live session", () => {
    resetDiagnosisStore({
      session: createLiveSession("sess-live-badges"),
      activeSessionId: "sess-live-badges",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-badges");

    expect(screen.getByText("Latency spike")).toBeInTheDocument();
    expect(screen.getByText("\u8bca\u65ad\u4e2d")).toBeInTheDocument();
    expect(screen.getByText("\u4e25\u91cd")).toBeInTheDocument();
    expect(screen.queryByText("\u5b9e\u65f6\u4f1a\u8bdd")).not.toBeInTheDocument();
    expect(screen.queryByText("sess-live-badges")).not.toBeInTheDocument();
    expect(screen.queryByText(/\u5b9e\u65f6\u94fe\u8def/u)).not.toBeInTheDocument();
  });

  it("shows realtime fallback badges without live business fields on /diagnosis", () => {
    resetDiagnosisStore();

    renderDemoPage();

    expect(screen.getByText("\u5b9e\u65f6\u4f1a\u8bdd")).toBeInTheDocument();
    expect(screen.getByText(/\u5b9e\u65f6\u94fe\u8def/u)).toBeInTheDocument();
    expect(screen.queryByText("Latency spike")).not.toBeInTheDocument();
    expect(screen.queryByText("\u8bca\u65ad\u4e2d")).not.toBeInTheDocument();
    expect(screen.queryByText("\u4e25\u91cd")).not.toBeInTheDocument();
  });

  it("keeps realtime fallback status bar before a live session is ready", () => {
    resetDiagnosisStore({
      session: undefined,
      activeSessionId: undefined,
      bootstrapStatus: "loading",
      traceStatus: "unknown",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-pending");

    expect(screen.getByText("\u5b9e\u65f6\u4f1a\u8bdd")).toBeInTheDocument();
    expect(screen.getByText(/\u5b9e\u65f6\u94fe\u8def/u)).toBeInTheDocument();
    expect(screen.queryByText("Latency spike")).not.toBeInTheDocument();
    expect(screen.queryByText("\u8bca\u65ad\u4e2d")).not.toBeInTheDocument();
    expect(screen.queryByText("\u4e25\u91cd")).not.toBeInTheDocument();
  });
});

describe("DiagnosisPage RCA report card", () => {
  const mockedBuildLiveView = vi.mocked(diagnosisModel.buildDiagnosisLiveView);

  beforeEach(() => {
    vi.clearAllMocks();
    resetDiagnosisStore({
      session: createLiveSession("sess-live-rca"),
      activeSessionId: "sess-live-rca",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("renders the restructured RCA card sections with localized labels", () => {
    const candidates = [
      {
        id: "candidate-1",
        title: "GPU \u4e89\u7528",
        summary: "GPU util \u6301\u7eed 99%",
        confidence: 0.91,
        confidenceLabel: "91%",
        statusLabel: "\u5df2\u786e\u8ba4",
        statusTone: "success",
        evidenceFor: ["GPU util \u6301\u7eed 99%"],
        evidenceAgainst: [],
        layer: "\u5e73\u53f0",
        entities: ["node:worker-03", "gpu:0"],
        rank: 1,
        evidenceSummary:
          "GPU util \u6301\u7eed 99%\uff0c\u8bf7\u6c42\u6392\u961f\u65f6\u957f\u540c\u6b65\u62ac\u5347\u3002",
        distinguishingVerification:
          "\u68c0\u67e5 worker-03 GPU \u8fdb\u7a0b\u662f\u5426\u5f02\u5e38\u5360\u7528\u3002",
        isPrimary: true,
      },
    ] satisfies DiagnosisCandidateView[];
    const hypotheses = [
      {
        id: "hyp-1",
        description: "GPU \u4e89\u7528",
        statusLabel: "\u5df2\u786e\u8ba4",
        statusTone: "success",
        evidenceForCount: 2,
        evidenceAgainstCount: 0,
        confidence: 0.91,
      },
    ] satisfies DiagnosisHypothesisView[];
    const propagationChain = [
      {
        id: "chain-1",
        entityId: "node:worker-03",
        entityType: "node",
        metric: "gpu_util",
        valueBefore: "82%",
        valueAfter: "99%",
        description: "\u5ef6\u8fdf\u5347\u9ad8",
      },
    ] satisfies DiagnosisPropagationStepView[];
    const summary = {
      title: "\u6839\u56e0\u8bca\u65ad",
      subtitle: "\u57fa\u4e8e\u73b0\u6709\u8bc1\u636e\u6574\u7406\u51fa\u7684\u5f53\u524d\u7ed3\u8bba\u3002",
      certaintyLabel: "\u5df2\u786e\u8ba4",
      certaintyTone: "success",
      confidenceLabel: "91%",
      confidenceRawLabel: "0.91",
      priorityLabel: "P1",
      sessionLabel: "sess-live-rca",
      updatedTimeLabel: "11:32:06",
      updatedDateTimeLabel: "2026/04/08 11:32:06",
      affectedServices: ["inference-gateway", "vllm-serving"],
      impactSummary:
        "vLLM p95 \u5ef6\u8fdf\u6301\u7eed\u8d70\u9ad8\uff0c\u63a8\u7406\u541e\u5410\u51fa\u73b0\u660e\u663e\u4e0b\u964d\u3002",
      rootCause: "worker-03 \u8282\u70b9 GPU \u4e89\u7528",
      rootCauseLayer: "platform",
      rootCauseLayerLabel: "\u5e73\u53f0",
      rootCauseEntities: ["node:worker-03", "gpu:0"],
    } satisfies DiagnosisDemoScenario["summary"];

    mockedBuildLiveView.mockReturnValue({
      timeline: [
        createReportItem({
          id: "live-report-rca",
          timestamp: "2026-04-08T11:32:06.000Z",
          summary,
          candidates,
          hypotheses,
          propagationChain,
        }),
      ],
      candidates,
      hypotheses,
      propagationChain,
      summary,
      plan: undefined,
    });

    renderLivePage("/diagnosis/sess-live-rca");

    expect(screen.getByTestId("diagnosis-report-card")).toBeInTheDocument();
    expect(screen.getAllByText(/\u6839\u56e0\u8bca\u65ad/u).length).toBeGreaterThan(0);
    expect(screen.getByText("\u5f53\u524d\u7ed3\u8bba")).toBeInTheDocument();
    expect(screen.getByText("\u5019\u9009\u6839\u56e0")).toBeInTheDocument();
    expect(screen.getByText("\u5f71\u54cd\u8303\u56f4")).toBeInTheDocument();
    expect(screen.getByText("worker-03 \u8282\u70b9 GPU \u4e89\u7528")).toBeInTheDocument();
    expect(screen.getAllByText(/GPU util/i).length).toBeGreaterThan(0 );
    fireEvent.click(screen.getByText("\u5c55\u5f00\u8bc1\u636e\u3001\u5047\u8bbe\u4e0e\u4f20\u64ad\u94fe\u8def"));

    expect(screen.getByText("\u5047\u8bbe\u4e0e\u8bc1\u636e")).toBeInTheDocument();
    expect(screen.getByText("\u4f20\u64ad\u94fe\u8def")).toBeInTheDocument();
    expect(
      screen.getByText(/\u533a\u5206\u9a8c\u8bc1\uff1a.*worker-03 GPU \u8fdb\u7a0b/u),
    ).toBeInTheDocument();
  });

  it("omits optional distinguishing verification and shows empty propagation copy", () => {
    const candidates = [
      {
        id: "candidate-2",
        title: "ECN \u914d\u7f6e\u4e0d\u4e00\u81f4",
        summary: "ECN \u95f4\u6b47\u6027\u6ce2\u52a8",
        confidence: 0.62,
        confidenceLabel: "62%",
        statusLabel: "\u9a8c\u8bc1\u4e2d",
        statusTone: "warning",
        evidenceFor: [],
        evidenceAgainst: [],
        entities: ["switch:tor-07"],
        rank: 1,
        evidenceSummary: "ECN \u6ce2\u52a8\u5b58\u5728\u95f4\u6b47\u6027\u5f02\u5e38\u3002",
        distinguishingVerification: null,
        isPrimary: true,
      },
    ] satisfies DiagnosisCandidateView[];
    const summary = {
      title: "\u6839\u56e0\u8bca\u65ad",
      subtitle: "demo",
      certaintyLabel: "\u5f85\u786e\u8ba4",
      certaintyTone: "warning",
      confidenceLabel: "62%",
      confidenceRawLabel: "0.62",
      priorityLabel: "P2",
      updatedTimeLabel: "11:40:00",
      updatedDateTimeLabel: "2026/04/08 11:40:00",
      affectedServices: [],
      impactSummary: "impact",
      rootCause: "ECN \u914d\u7f6e\u4e0d\u4e00\u81f4",
    } satisfies DiagnosisDemoScenario["summary"];

    mockedBuildLiveView.mockReturnValue({
      timeline: [
        createReportItem({
          id: "live-report-rca-empty",
          timestamp: "2026-04-08T11:40:00.000Z",
          summary,
          candidates,
          hypotheses: [],
          propagationChain: [],
        }),
      ],
      candidates,
      hypotheses: [],
      propagationChain: [],
      summary,
      plan: undefined,
    });

    renderLivePage("/diagnosis/sess-live-rca");

    expect(screen.queryByText(/\u533a\u5206\u9a8c\u8bc1/u)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("\u5c55\u5f00\u8bc1\u636e\u3001\u5047\u8bbe\u4e0e\u4f20\u64ad\u94fe\u8def"));

    expect(screen.getByText("\u6682\u65e0\u4f20\u64ad\u94fe\u8def\u6570\u636e\u3002")).toBeInTheDocument();
  });
});






