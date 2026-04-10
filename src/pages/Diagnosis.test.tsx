import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisLocalAuditRecord, DiagnosisSession } from "../api/types";
import { useDiagnosisStore } from "../store/diagnosisStore";
import DiagnosisPage from "./Diagnosis";
import * as diagnosisModel from "./diagnosisModel";
import type { DiagnosisDemoScenario, DiagnosisTimelineItem } from "./diagnosisModel";

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

function createApprovalSession(sessionId: string): DiagnosisSession {
  return {
    ...createLiveSession(sessionId),
    status: "approval_required",
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

    const input = screen.getByPlaceholderText(/Ask the agent to diagnose an issue/i);
    fireEvent.change(input, { target: { value: "demo request" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(120);

    expect(container.querySelectorAll(".diagnosis-workspace-message-row")).toHaveLength(2);
    expect(screen.queryByText("second-stream")).not.toBeInTheDocument();

    await flushPendingTimers();

    expect(container.querySelectorAll(".diagnosis-workspace-message-row")).toHaveLength(3);
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

    const input = screen.getByPlaceholderText(/Ask the agent to diagnose an issue/i);
    fireEvent.change(input, { target: { value: "demo thinking" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

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

    const input = screen.getByPlaceholderText(/Ask the agent to diagnose an issue/i);
    fireEvent.change(input, { target: { value: "check tool sequence" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

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

    const input = screen.getByPlaceholderText(/Ask the agent to diagnose an issue/i);
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

    const input = screen.getByPlaceholderText(/Ask the agent to diagnose an issue/i);
    fireEvent.change(input, { target: { value: "validate thinking then tool" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(600);

    expect(screen.queryByText("query_service_metrics")).not.toBeInTheDocument();

    await flushPendingTimers();

    expect(screen.getByText("query_service_metrics")).toBeInTheDocument();
  });

  it("auto shows the demo approval card after the RCA report card", async () => {
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

    const input = screen.getByPlaceholderText(/Ask the agent to diagnose an issue/i);
    fireEvent.change(input, { target: { value: "show demo approval" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    expect(screen.queryByTestId("diagnosis-demo-approval-card")).not.toBeInTheDocument();

    await advance(5000);
    await flushPendingTimers();
    await advance(500);

    const approvalCard = screen.getByTestId("diagnosis-demo-approval-card");
    const composerAnchor = container.querySelector(
      ".diagnosis-workspace-composer-anchor--with-approval",
    );

    expect(approvalCard).toHaveClass(
      "diagnosis-workspace-approval-surface--floating",
    );
    expect(composerAnchor).not.toBeNull();
    expect(
      container.querySelector(".diagnosis-workspace-feed .diagnosis-workspace-approval-surface"),
    ).toBeNull();
    expect(
      composerAnchor?.querySelector('[data-testid="diagnosis-demo-approval-card"]'),
    ).toBe(approvalCard);
    expect(screen.queryByText("kubectl | wait 120s | verify wait")).not.toBeInTheDocument();
    expect(screen.getByText("Drain canary first")).toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-demo-approve-button")).toBeInTheDocument();
  });

  it("marks a live loading tool as timeout after 15 seconds and continues the queue", async () => {
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
    expect(screen.queryByText("incremental-two")).not.toBeInTheDocument();

    await advance(5000);

    expect(screen.getByText("incremental-one")).toBeInTheDocument();
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
      timeline: [],
      candidates: [],
      summary: undefined,
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
    expect(container.querySelector(".diagnosis-workspace-approval-card")).toBeNull();
  });

  it("requires a reject reason and appends a system audit item after rejection", async () => {
    mockedBuildLiveView.mockImplementation((_, __, ___, localAuditRecords = []) => ({
      timeline: (localAuditRecords as DiagnosisLocalAuditRecord[]).map((record) => ({
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
      candidates: [],
      summary: undefined,
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
      expect(screen.getByText(/\[system\] approval rejected \(reason:/)).toBeInTheDocument();
    });
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

  it("keeps the demo badge without live business fields", () => {
    resetDiagnosisStore();

    renderDemoPage();

    expect(screen.getByText("\u6f14\u793a\u6a21\u5f0f")).toBeInTheDocument();
    expect(screen.queryByText("Latency spike")).not.toBeInTheDocument();
    expect(screen.queryByText("\u8bca\u65ad\u4e2d")).not.toBeInTheDocument();
    expect(screen.queryByText("\u4e25\u91cd")).not.toBeInTheDocument();
  });

  it("keeps the fallback status bar before a live session is ready", () => {
    resetDiagnosisStore({
      session: undefined,
      activeSessionId: undefined,
      bootstrapStatus: "loading",
      traceStatus: "unknown",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-pending");

    expect(screen.getByText("\u6f14\u793a\u6a21\u5f0f")).toBeInTheDocument();
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
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [
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
          evidenceSummary: "GPU util \u6301\u7eed 99%\uff0c\u8bf7\u6c42\u6392\u961f\u65f6\u957f\u540c\u6b65\u62ac\u5347\u3002",
          distinguishingVerification: "\u68c0\u67e5 worker-03 GPU \u8fdb\u7a0b\u662f\u5426\u5f02\u5e38\u5360\u7528\u3002",
          isPrimary: true,
        },
      ],
      hypotheses: [
        {
          id: "hyp-1",
          description: "GPU \u4e89\u7528",
          statusLabel: "\u5df2\u786e\u8ba4",
          statusTone: "success",
          evidenceForCount: 2,
          evidenceAgainstCount: 0,
          confidence: 0.91,
        },
      ],
      propagationChain: [
        {
          id: "chain-1",
          entityId: "node:worker-03",
          entityType: "node",
          metric: "gpu_util",
          valueBefore: "82%",
          valueAfter: "99%",
          description: "\u5ef6\u8fdf\u5347\u9ad8",
        },
      ],
      summary: {
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
        impactSummary: "vLLM p95 \u5ef6\u8fdf\u6301\u7eed\u8d70\u9ad8\uff0c\u63a8\u7406\u541e\u5410\u51fa\u73b0\u660e\u663e\u4e0b\u964d\u3002",
        rootCause: "worker-03 \u8282\u70b9 GPU \u4e89\u7528",
        rootCauseLayer: "platform",
        rootCauseLayerLabel: "\u5e73\u53f0",
        rootCauseEntities: ["node:worker-03", "gpu:0"],
      },
      plan: undefined,
    });

    renderLivePage("/diagnosis/sess-live-rca");

    expect(screen.getAllByText("\u6839\u56e0\u8bca\u65ad").length).toBeGreaterThan(0);
    expect(screen.getByText("\u5f53\u524d\u7ed3\u8bba")).toBeInTheDocument();
    expect(screen.getByText("\u5019\u9009\u6839\u56e0\uff081\uff09")).toBeInTheDocument();
    expect(screen.getByText("\u5047\u8bbe\u4e0e\u8bc1\u636e")).toBeInTheDocument();
    expect(screen.getByText("\u4f20\u64ad\u94fe\u8def\uff08\u6298\u53e0\uff09")).toBeInTheDocument();
    expect(screen.getByText("worker-03 \u8282\u70b9 GPU \u4e89\u7528")).toBeInTheDocument();
    expect(screen.getByText(/GPU util \u6301\u7eed 99%\uff0c\u8bf7\u6c42\u6392\u961f\u65f6\u957f\u540c\u6b65\u62ac\u5347\u3002/u)).toBeInTheDocument();
    expect(screen.getByText(/\u533a\u5206\u9a8c\u8bc1\uff1a.*worker-03 GPU \u8fdb\u7a0b/u)).toBeInTheDocument();
  });

  it("omits optional distinguishing verification and shows empty propagation copy", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [
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
      ],
      hypotheses: [],
      propagationChain: [],
      summary: {
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
      },
      plan: undefined,
    });

    renderLivePage("/diagnosis/sess-live-rca");

    expect(screen.queryByText(/\u533a\u5206\u9a8c\u8bc1/u)).not.toBeInTheDocument();
    expect(screen.getByText("\u6682\u65e0\u4f20\u64ad\u94fe\u8def\u6570\u636e\u3002")).toBeInTheDocument();
  });
});





