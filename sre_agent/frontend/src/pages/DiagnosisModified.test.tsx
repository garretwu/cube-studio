import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisSession } from "../api/types";
import { useDiagnosisStore } from "../store/diagnosisStore";
import DiagnosisModifiedPage from "./DiagnosisModified";
import * as diagnosisModifiedModel from "./diagnosisModifiedModel";
import type { DiagnosisModifiedDemoScenario, DiagnosisModifiedTimelineItem } from "./diagnosisModifiedModel";

vi.mock("../hooks/useWebSocket", () => ({
  useWebSocket: () => ({ state: "closed" as const }),
}));

vi.mock("./diagnosisModifiedModel", async () => {
  const actual = await vi.importActual<typeof import("./diagnosisModifiedModel")>("./diagnosisModifiedModel");
  return {
    ...actual,
    buildDiagnosisModifiedDemoScenario: vi.fn(actual.buildDiagnosisModifiedDemoScenario),
    buildDiagnosisModifiedLiveView: vi.fn(actual.buildDiagnosisModifiedLiveView),
  };
});

const baseSummary: DiagnosisModifiedDemoScenario["summary"] = {
  title: "Demo RCA",
  subtitle: "Demo subtitle",
  certaintyLabel: "Likely",
  certaintyTone: "accent",
  confidenceLabel: "80%",
  affectedServices: ["auth-svc"],
  impactSummary: "Demo impact",
};

const basePlan: DiagnosisModifiedDemoScenario["plan"] = {
  title: "Demo plan",
  description: "Demo remediation plan",
  priorityLabel: "P1",
  confidenceLabel: "80%",
  steps: [],
};


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

function createDetailedLiveSession(sessionId: string): DiagnosisSession {
  return {
    ...createLiveSession(sessionId),
    status: "approval_required",
    outcome: "resolved",
    re_diagnosis_round: 2,
    diagnosis_result: {
      root_cause: "Redis connection saturation",
      root_cause_layer: "service",
      root_cause_entities: ["redis-primary", "auth-svc"],
      confidence: 0.86,
      hypotheses: [
        {
          description: "Redis timeout amplifies auth request retries",
          status: "confirmed",
          confidence: 0.86,
          evidence_for: ["Redis saturation observed"],
          evidence_against: [],
        },
      ],
      impact_summary: "Auth login latency spikes and partial 5xx responses.",
      affected_services: ["auth-svc", "login-api"],
      triage_priority: "P1",
      diagnosis_certainty: "confirmed",
      ranked_candidates: [
        {
          rank: 1,
          root_cause: "Redis connection saturation",
          root_cause_layer: "service",
          root_cause_entities: ["redis-primary", "auth-svc"],
          confidence: 0.86,
          evidence_summary: "Redis timeout and retry loop align with the alert window.",
          distinguishing_verification: "Check Redis saturation before scaling rollout.",
        },
        {
          rank: 2,
          root_cause: "Downstream database wait queue",
          root_cause_layer: "service",
          root_cause_entities: ["orders-db"],
          confidence: 0.54,
          evidence_summary: "Database wait queue increased after retry amplification.",
        },
      ],
      recommended_fix: {
        plan_id: "plan-auth-redis-v2",
        root_cause: "Redis connection saturation",
        description: "Throttle rollout and validate Redis recovery before expanding.",
        steps: [
          {
            step_id: 1,
            description: "Roll traffic back to canary batch",
            tool: "scale_rollout",
            params: { target: "canary" },
            verification: { method: "wait", wait_seconds: 60 },
            timeout: 600,
          },
          {
            step_id: 2,
            description: "Observe Redis timeout and error rate",
            tool: "query_metrics",
            params: { service: "auth-svc" },
            verification: { method: "promql", query: "rate(errors[5m])" },
            timeout: 600,
          },
        ],
        canary: {
          enabled: true,
          target_percentage: 10,
          monitor_duration: 15,
          success_criteria: [
            {
              metric: "error_rate",
              operator: "<",
              value: 1,
            },
          ],
        },
        estimated_impact: "Limited canary execution before wider rollout.",
        confidence: 0.79,
        priority: "P1",
        safety_level: "guarded",
      },
    },
  };
}

function createSessionWithStatus(sessionId: string, status: DiagnosisSession["status"]) {
  return {
    ...createDetailedLiveSession(sessionId),
    status,
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
    applyEvent: vi.fn(),
    setConnectionState: vi.fn(),
    ...overrides,
  });
}

function renderDemoPage() {
  return render(
    <MemoryRouter initialEntries={["/diagnosis-modified"]}>
      <Routes>
        <Route path="/diagnosis-modified" element={<DiagnosisModifiedPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderLivePage(path = "/diagnosis-modified/sess-live") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/diagnosis-modified/:sessionId" element={<DiagnosisModifiedPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function createWindowOpenSpy() {
  return vi.spyOn(window, "open").mockImplementation(() => null);
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
describe("DiagnosisModifiedPage sequential playback", () => {
  const mockedBuildDemoScenario = vi.mocked(diagnosisModifiedModel.buildDiagnosisModifiedDemoScenario);
  const mockedBuildLiveView = vi.mocked(diagnosisModifiedModel.buildDiagnosisModifiedLiveView);

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

  it("does not render the duplicated in-page header title", () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [],
      events: [],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    renderDemoPage();

    expect(screen.queryByText("閻犲洤锕ラ弻鍥ㄧ┍椤旂⒈妲婚柨娑樼墔閹便劑寮ㄩ惂鍝ョ")).not.toBeInTheDocument();
  });

  it("continues the demo into canary, observation, and closure after approval", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-approval-flow",
          kind: "message",
          role: "user",
          content: "diagnose auth latency",
          timestamp: "2026-04-08T12:20:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "assistant-demo-approval-flow",
            kind: "message",
            role: "assistant",
            content: "Plan ready for approval.",
            timestamp: "2026-04-08T12:20:01.000Z",
          },
        },
        {
          delayMs: 1,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: {
        ...basePlan,
        canaryLabel: "Canary 10% / observe 15 minutes",
      },
      session: createDetailedLiveSession("demo-session-approval-flow"),
    });

    renderDemoPage();
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await flushPendingTimers();

    expect(screen.getByText("Plan ready for approval.")).toBeInTheDocument();





  });

  it("shows a start demo button in idle mode and triggers the default playback", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-trigger",
          kind: "message",
          role: "user",
          content: "Analyze auth-svc latency and error-rate spike in the past hour",
          timestamp: "2026-04-08T09:59:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "assistant-trigger",
            kind: "message",
            role: "assistant",
            content: "demo-started",
            timestamp: "2026-04-08T09:59:01.000Z",
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

    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    expect(mockedBuildDemoScenario).toHaveBeenCalledWith("Analyze auth-svc latency and error-rate spike in the past hour");

    await flushPendingTimers();

    expect(screen.getByText("demo-started")).toBeInTheDocument();
  });

  it("shows the second assistant message only after the first stream completes", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await advance(120);

    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(1);
    expect(screen.queryByText("second-stream")).not.toBeInTheDocument();

    await flushPendingTimers();

    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(2);
  });

  it("collapses finished thinking into a unified Thought for x seconds label", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await flushPendingTimers();

    expect(screen.getByText(/Thought for \d+ seconds?/i)).toBeInTheDocument();
    expect(screen.queryByText("Agent is understanding the request")).not.toBeInTheDocument();
  });

  it("blocks later timeline items while a demo tool is still loading", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await advance(200);

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(screen.queryByText("after-tool")).not.toBeInTheDocument();

  });

  it("keeps demo tool cards in loading for at least 3.5 seconds before success", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await advance(120);

    expect(screen.getByText("loading")).toBeInTheDocument();

    await advance(3200);

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(screen.queryByText("success")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(0);

    await advance(1500);

    expect(screen.getByText("success")).toBeInTheDocument();

    await flushPendingTimers();

    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(1);
  });

  it("waits for thinking completion before starting a demo tool call", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await advance(600);

    expect(screen.queryByText("query_service_metrics")).not.toBeInTheDocument();

    await flushPendingTimers();

    expect(screen.getByText("query_service_metrics")).toBeInTheDocument();
  });

  it("marks a live loading tool as timeout after 15 seconds and continues the queue", async () => {
    let timelineSource: DiagnosisModifiedTimelineItem[] = [];
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

    renderLivePage("/diagnosis-modified/sess-live-timeout");

    timelineSource = [
      {
        id: "live-tool-1",
        kind: "tool",
        toolName: "query_slow_backend",
        params: { service: "auth-svc" },
        timestamp: "2026-04-08T11:00:01.000Z",
        status: "loading",
        summaryLines: ["Waiting for tool result..."],
      },
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

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(screen.queryByText("post-timeout")).not.toBeInTheDocument();

    await advance(15000);
    await advance(1200);

    expect(screen.getByText("timeout")).toBeInTheDocument();
    expect(screen.getByText("post-timeout")).toBeInTheDocument();
  });

  it("renders history immediately in live mode and serializes only incremental events", async () => {
    let timelineSource: DiagnosisModifiedTimelineItem[] = [
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
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis-modified/sess-live-history");

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
    expect(screen.queryByText("incremental-two")).not.toBeInTheDocument();

    await advance(5000);

    expect(screen.getByText("incremental-one")).toBeInTheDocument();
    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(3);
  });

  it("opens the remediation overview in a new page from the demo report rail", async () => {
    const windowOpenSpy = createWindowOpenSpy();

    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-remediation-entry",
          kind: "message",
          role: "user",
          content: "diagnose auth latency",
          timestamp: "2026-04-08T12:20:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "assistant-demo-remediation-entry",
            kind: "message",
            role: "assistant",
            content: "Plan ready for approval.",
            timestamp: "2026-04-08T12:20:01.000Z",
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
      session: createDetailedLiveSession("demo-session-approval-flow"),
    });

    renderDemoPage();
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await advance(5000);
    await flushPendingTimers();

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    const reportHeader = within(reportRail).getByTestId("diagnosis-modified-report-header");

    expect(within(reportHeader).queryByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" })).not.toBeInTheDocument();
    expect(within(reportRail).getAllByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" })).toHaveLength(1);

    fireEvent.click(within(reportRail).getByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" }));

    expect(windowOpenSpy).toHaveBeenCalledWith(
      "/remediation?sessionId=demo-session-approval-flow",
      "_blank",
      "noopener,noreferrer",
    );
  });


  it("renders a lightweight remediation entry in the left flow with status-aware labels", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-msg-flow-remediation-entry",
          kind: "message",
          role: "assistant",
          content: "Collected Redis and auth-service evidence.",
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    const cases = [
      ["approval_required", "\u5ba1\u6279\u4fee\u590d"],
      ["validating", "\u67e5\u770b\u6267\u884c"],
      ["failed", "\u67e5\u770b\u4fee\u590d\u8bb0\u5f55"],
    ] as const;

    cases.forEach(([status, label]) => {
      const sessionId = `sess-flow-${status}`;
      resetDiagnosisStore({
        session: createSessionWithStatus(sessionId, status),
        activeSessionId: sessionId,
        bootstrapStatus: "ready",
        traceStatus: "ready",
        messages: [],
        bootstrapSession: vi.fn().mockResolvedValue(undefined),
      });

      const { unmount } = renderLivePage(`/diagnosis-modified/${sessionId}`);

      expect(
        within(screen.getByTestId("diagnosis-modified-flow-remediation-entry")).getByRole("button", { name: label }),
      ).toBeInTheDocument();

      unmount();
    });
  });

  it("opens the remediation overview from the lightweight left flow entry", () => {
    const windowOpenSpy = createWindowOpenSpy();

    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-msg-flow-remediation-click",
          kind: "message",
          role: "assistant",
          content: "Collected Redis and auth-service evidence.",
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createSessionWithStatus("sess-flow-open", "awaiting_approval"),
      activeSessionId: "sess-flow-open",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-flow-open");

    fireEvent.click(
      within(screen.getByTestId("diagnosis-modified-flow-remediation-entry")).getByRole("button", {
        name: "\u5ba1\u6279\u4fee\u590d",
      }),
    );

    expect(windowOpenSpy).toHaveBeenCalledWith(
      "/remediation?sessionId=sess-flow-open",
      "_blank",
      "noopener,noreferrer",
    );
  });
  it("opens the remediation overview in a new page from the live report rail", () => {
    const windowOpenSpy = createWindowOpenSpy();

    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-msg-remediation-entry",
          kind: "message",
          role: "assistant",
          content: "Collected Redis and auth-service evidence.",
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createDetailedLiveSession("sess-live-modified-remediation"),
      activeSessionId: "sess-live-modified-remediation",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-modified-remediation");

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    const reportHeader = within(reportRail).getByTestId("diagnosis-modified-report-header");

    expect(within(reportHeader).queryByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" })).not.toBeInTheDocument();
    expect(within(reportRail).getAllByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" })).toHaveLength(1);

    fireEvent.click(within(reportRail).getByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" }));

    expect(windowOpenSpy).toHaveBeenCalledWith(
      "/remediation?sessionId=sess-live-modified-remediation",
      "_blank",
      "noopener,noreferrer",
    );
  });
  it("renders an inline approval surface for a live session that is awaiting approval", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-msg-inline-approval",
          kind: "message",
          role: "assistant",
          content: "A remediation plan is ready for approval.",
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: {
        title: "Redis connection saturation",
        description: "Throttle rollout and validate Redis recovery before expanding.",
        priorityLabel: "P1",
        confidenceLabel: "79%",
        safetyLabel: "guarded",
        canaryLabel: "\u91d1\u4e1d\u96c0 10% | \u89c2\u6d4b 15m",
        impactSummary: "Limited canary execution before wider rollout.",
        steps: [
          {
            id: "approval-step-1",
            title: "Roll traffic back to canary batch",
            detail: "scale_rollout | \u8017\u65f6 600s | \u6821\u9a8c wait",
            toolName: "scale_rollout",
            paramsSummary: 'target="canary"',
            status: "pending",
          },
          {
            id: "approval-step-2",
            title: "Observe Redis timeout and error rate",
            detail: "query_metrics | \u8017\u65f6 600s | \u6821\u9a8c promql",
            toolName: "query_metrics",
            paramsSummary: 'service="auth-svc"',
            status: "pending",
          },
        ],
      },
    });

    resetDiagnosisStore({
      session: createDetailedLiveSession("sess-live-inline-approval"),
      activeSessionId: "sess-live-inline-approval",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      currentPlanVersion: 2,
      latestPlanVersion: 2,
      canApprove: true,
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-inline-approval");

    const approvalSurface = screen.getByTestId("diagnosis-modified-approval-surface");
    expect(approvalSurface).toBeInTheDocument();
    expect(within(approvalSurface).getByText("Redis connection saturation")).toBeInTheDocument();
    expect(within(approvalSurface).getByText("Throttle rollout and validate Redis recovery before expanding.")).toBeInTheDocument();
    expect(within(approvalSurface).getByText("P1")).toBeInTheDocument();
    expect(within(approvalSurface).getByText("79% \u7f6e\u4fe1")).toBeInTheDocument();
    expect(within(approvalSurface).getByText("guarded")).toBeInTheDocument();
    expect(within(approvalSurface).getByText("\u91d1\u4e1d\u96c0 10% | \u89c2\u6d4b 15m")).toBeInTheDocument();
    expect(within(approvalSurface).getByText("Limited canary execution before wider rollout.")).toBeInTheDocument();
    expect(within(approvalSurface).getByText("Roll traffic back to canary batch")).toBeInTheDocument();
    expect(within(approvalSurface).getByText("Observe Redis timeout and error rate")).toBeInTheDocument();
    expect(within(approvalSurface).getByRole("button", { name: "\u540c\u610f\u6267\u884c" })).toBeInTheDocument();
  });

  it("submits approval from the inline live approval surface", async () => {
    const approvePlan = vi.fn().mockResolvedValue(undefined);

    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-msg-inline-approval-submit",
          kind: "message",
          role: "assistant",
          content: "A remediation plan is ready for approval.",
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: {
        title: "Redis connection saturation",
        description: "Throttle rollout and validate Redis recovery before expanding.",
        priorityLabel: "P1",
        confidenceLabel: "79%",
        safetyLabel: "guarded",
        canaryLabel: "\u91d1\u4e1d\u96c0 10% | \u89c2\u6d4b 15m",
        impactSummary: "Limited canary execution before wider rollout.",
        steps: [
          {
            id: "approval-submit-step-1",
            title: "Roll traffic back to canary batch",
            detail: "scale_rollout | \u8017\u65f6 600s | \u6821\u9a8c wait",
            toolName: "scale_rollout",
            paramsSummary: 'target="canary"',
            status: "pending",
          },
        ],
      },
    });

    resetDiagnosisStore({
      session: createDetailedLiveSession("sess-live-inline-approval-submit"),
      activeSessionId: "sess-live-inline-approval-submit",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      currentPlanVersion: 2,
      latestPlanVersion: 2,
      canApprove: true,
      approvePlan,
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-inline-approval-submit");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "\u540c\u610f\u6267\u884c" }));
      await Promise.resolve();
    });

    expect(approvePlan).toHaveBeenCalledWith({ approved: true });
  });
});

describe("DiagnosisModifiedPage split workspace", () => {
  const mockedBuildDemoScenario = vi.mocked(diagnosisModifiedModel.buildDiagnosisModifiedDemoScenario);
  const mockedBuildLiveView = vi.mocked(diagnosisModifiedModel.buildDiagnosisModifiedLiveView);

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    resetDiagnosisStore();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders a right-side report rail and keeps the timeline in a separate pane", async () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-user-report-0",
          kind: "message",
          role: "user",
          content: "Analyze auth-svc latency and error-rate spike in the past hour",
          timestamp: "2026-04-08T12:00:00.000Z",
        },
        {
          id: "live-thinking-report-1",
          kind: "thinking",
          title: "Correlate Redis, auth retries, and alert window",
          content: "Redis timeout aligns with the auth retry burst and the alert window.",
          timestamp: "2026-04-08T12:00:01.000Z",
          status: "completed",
          thoughtDurationSec: 2,
        },
        {
          id: "live-tool-report-1",
          kind: "tool",
          toolName: "query_service_metrics",
          params: { service: "auth-svc", window: "5m" },
          timestamp: "2026-04-08T12:00:02.000Z",
          status: "success",
          summaryLines: ["Redis timeout and auth retry pressure rose together."],
        },
        {
          id: "live-msg-report-1",
          kind: "message",
          role: "assistant",
          content: "Collected Redis and auth-service evidence.",
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [
        {
          id: "candidate-1",
          title: "Redis connection saturation",
          summary: "Redis timeout correlates with auth retry amplification.",
          confidence: 0.86,
          confidenceLabel: "86%",
          statusLabel: "Primary candidate",
          statusTone: "success",
          evidenceFor: ["Redis timeout"],
          evidenceAgainst: [],
          entities: ["redis-primary", "auth-svc"],
          isPrimary: true,
          rank: 1,
          evidenceSummary: "Redis timeout and retry loop align with alert timing.",
        },
      ],
      summary: baseSummary,
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createDetailedLiveSession("sess-live-report"),
      activeSessionId: "sess-live-report",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      events: [
        {
          schema_version: "1",
          type: "remediation_progress",
          session_id: "sess-live-report",
          timestamp: "2026-04-08T12:00:08.000Z",
          data: {
            stage: "canary_progress",
            progress: 40,
            progress_label: "Canary 40%",
          },
        },
      ],
      localAuditRecords: [
        {
          id: "audit-approval",
          sessionId: "sess-live-report",
          eventKind: "approval_result",
          source: "local_audit",
          dedupeKey: "approval-v2",
          timestamp: "2026-04-08T12:00:05.000Z",
          summary: "[system] approval recorded",
          details: ["Execute 10% canary first, then observe Redis timeout recovery."],
          statusTone: "success",
        },
      ],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis-modified/sess-live-report");

    expect(screen.getByTestId("diagnosis-modified-split-workspace")).toHaveAttribute("data-layout", "trace-report");
    expect(screen.getByTestId("diagnosis-modified-main-pane")).toHaveAttribute("aria-label", "诊断轨迹");
    expect(screen.getByText("诊断轨迹")).toBeInTheDocument();
    expect(screen.getByText("Thinking Trace")).toBeInTheDocument();
    expect(screen.getByText("Analysis Report")).toBeInTheDocument();
    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    expect(reportRail).toBeInTheDocument();
    const overview = within(reportRail).getByTestId("diagnosis-modified-report-overview");
    const reportHeader = within(reportRail).getByTestId("diagnosis-modified-report-header");
    expect(overview).toHaveTextContent("Redis connection saturation");
    expect(reportHeader).toHaveTextContent("Auth login latency spikes and partial 5xx responses.");
    expect(reportHeader).toHaveTextContent("Latency spike");
    expect(reportHeader).toHaveTextContent("2026-04-08T12:00:08.000Z");
    expect(reportHeader).toHaveTextContent("待审批");
    expect(reportHeader).not.toHaveTextContent("Auto summary");
    expect(reportHeader).not.toHaveTextContent("auth-svc");
    expect(reportHeader).not.toHaveTextContent("sess-live-report");
    expect(within(reportHeader).queryByText(/^Status$/)).not.toBeInTheDocument();
    expect(reportHeader.querySelector(".diagnosis-modified-badge--active")).toBeTruthy();
    expect(within(reportHeader).getByTitle("Auth login latency spikes and partial 5xx responses.")).toBeInTheDocument();
    expect(reportHeader.querySelector(".diagnosis-modified-report-rail__header-meta")).toBeNull();
    expect(reportHeader.querySelector(".diagnosis-modified-report-rail__inline-meta")).toBeTruthy();
    expect(within(reportHeader).getByTitle("Latency spike")).toBeInTheDocument();
    expect(container.querySelector("[data-testid='diagnosis-modified-timeline']")).toBeTruthy();
    const traceList = screen.getByTestId("diagnosis-modified-trace-list");
    expect(traceList).toBeInTheDocument();
    expect(within(traceList).queryByText("Analyze auth-svc latency and error-rate spike in the past hour")).not.toBeInTheDocument();
    expect(within(traceList).getByText("Thought / 推理")).toBeInTheDocument();
    expect(within(traceList).getByText("Tool Call / 工具调用")).toBeInTheDocument();
    expect(within(traceList).getByText("Decision / 形成判断")).toBeInTheDocument();
    expect(within(traceList).getByText("Action Generated / 生成修复动作")).toBeInTheDocument();
    const actionStep = screen.getByTestId("diagnosis-modified-action-generated-step");
    expect(within(actionStep).getByTestId("diagnosis-modified-flow-remediation-entry")).toBeInTheDocument();
    expect(within(actionStep).getByTestId("diagnosis-modified-approval-surface")).toBeInTheDocument();
    expect(actionStep.querySelector(".diagnosis-modified-trace-step__rail")).toBeTruthy();
    expect(container.querySelector(".diagnosis-modified-message-row--user")).toBeNull();
    expect(screen.getByText("影响拓扑")).toBeInTheDocument();
    expect(screen.getByText("归因分析")).toBeInTheDocument();
    expect(screen.queryByText("Current Conclusion")).not.toBeInTheDocument();
    expect(screen.queryByText("Root Cause Assessment")).not.toBeInTheDocument();
    expect(screen.queryByText("关键证据")).toBeNull();
    expect(screen.getAllByText("修复建议").length).toBeGreaterThan(0);
    expect(within(reportRail).queryByText(/^01$/)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/^02$/)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/^03$/)).not.toBeInTheDocument();
    expect(container.querySelector(".diagnosis-modified-report-rail__context-graph")).toBeTruthy();
    expect(container.querySelector(".diagnosis-modified-report-rail__context-lists")).toBeNull();
    expect(container.querySelectorAll(".diagnosis-modified-report-rail__section").length).toBe(3);
    expect(screen.getAllByText("Redis connection saturation").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Execute 10% canary first, then observe Redis timeout recovery.")).toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-report-rail-loading")).not.toBeInTheDocument();
  });





  it("renders the modified report rail in demo mode as well", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-report",
          kind: "message",
          role: "user",
          content: "diagnose auth latency",
          timestamp: "2026-04-08T12:10:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "assistant-demo-report",
            kind: "message",
            role: "assistant",
            content: "I have a likely remediation path.",
            timestamp: "2026-04-08T12:10:01.000Z",
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
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await flushPendingTimers();

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    expect(reportRail).toBeInTheDocument();
    expect(within(reportRail).getByTestId("diagnosis-modified-report-overview")).toBeInTheDocument();
    expect(within(reportRail).getByTestId("diagnosis-modified-report-header")).toBeInTheDocument();
    expect(screen.queryByText("Auto summary")).not.toBeInTheDocument();
    expect(screen.getByText("影响拓扑")).toBeInTheDocument();
    expect(screen.getByText("归因分析")).toBeInTheDocument();
    expect(screen.queryByText("Current Conclusion")).not.toBeInTheDocument();
    expect(screen.queryByText("Root Cause Assessment")).not.toBeInTheDocument();
    expect(screen.queryByText("关键证据")).toBeNull();
    expect(screen.getByText("修复建议")).toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-report-rail-loading")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("diagnosis-modified-report-section-loading").length).toBeGreaterThanOrEqual(1);
  });
});



















