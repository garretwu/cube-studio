import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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

    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(2);
    expect(screen.queryByText("second-stream")).not.toBeInTheDocument();

    await flushPendingTimers();

    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(3);
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
    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(1);

    await advance(1500);

    expect(screen.getByText("success")).toBeInTheDocument();

    await flushPendingTimers();

    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(2);
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
});



















