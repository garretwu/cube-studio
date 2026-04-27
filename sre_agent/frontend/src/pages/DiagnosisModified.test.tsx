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
    alertSnapshot: null,
    topologyContext: null,
    completedThinkingRounds: [],
    liveThinking: null,
    liveFinalAnswer: null,
    streamingText: "",
    streamingNode: null,
    isStreamingDiagnosis: false,
    streamingPhase: "idle",
    streamSequenceCounter: 0,
    roundSequenceCounter: 0,
    activeStreamingTools: [],
    streamingAbortController: null,
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

    await advance(500);
    await flushPendingTimers();

    expect(screen.getByText("Plan ready for approval.")).toBeInTheDocument();
    const approvalSurface = screen.getByTestId("diagnosis-modified-approval-surface");
    fireEvent.click(within(approvalSurface).getByRole("button", { name: "同意执行" }));

    await advance(10_000);
    await flushPendingTimers();

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    const traceList = screen.getByTestId("diagnosis-modified-trace-list");
    const actionStep = screen.getByTestId("diagnosis-modified-action-generated-step");

    expect(screen.getByText("审批已通过，系统已记录修复执行指令。")).toBeInTheDocument();
    expect(screen.getByText("修复执行已启动：10% 灰度验证进行中。")).toBeInTheDocument();
    expect(screen.getByText("指标反馈：延迟与错误率回落到受控范围。")).toBeInTheDocument();
    expect(screen.getByText("告警恢复：主告警已恢复，准备关闭诊断会话。")).toBeInTheDocument();
    expect(screen.getByText("会话关闭：诊断与修复验证已完成收口。")).toBeInTheDocument();
    const firstStatusSync = within(traceList).getAllByText("修复状态同步")[0]?.closest("li");
    expect(within(traceList).getAllByText("修复状态同步").length).toBeGreaterThanOrEqual(5);
    expect(firstStatusSync).not.toBeNull();
    expect(screen.queryByText(/\[system\] approval recorded/i)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("指标反馈")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("告警恢复")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("会话关闭")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/Session closed after verification/i)).not.toBeInTheDocument();
    expect(within(actionStep).getByRole("button", { name: "查看修复记录" })).toBeInTheDocument();
    expect(
      Boolean(
        firstStatusSync &&
          (actionStep.compareDocumentPosition(firstStatusSync) & Node.DOCUMENT_POSITION_FOLLOWING),
      ),
    ).toBe(true);
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

  it("renders a structured empty report preview before diagnosis starts", () => {
    renderDemoPage();

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    const reportHeader = within(reportRail).getByTestId("diagnosis-modified-report-header");
    const reportBody = within(reportRail).getByTestId("diagnosis-modified-report-body");

    expect(screen.getByText("Analysis Report")).toBeInTheDocument();
    expect(within(reportHeader).getByText("诊断报告")).toBeInTheDocument();
    expect(
      within(reportHeader).getByText("诊断开始后，将在此持续生成结构化分析结论与修复建议"),
    ).toBeInTheDocument();
    expect(within(reportHeader).getByText("未开始")).toBeInTheDocument();
    expect(within(reportHeader).queryByText("处理中")).not.toBeInTheDocument();
    expect(within(reportHeader).queryByText("Updated")).not.toBeInTheDocument();

    expect(reportBody).toHaveClass("diagnosis-modified-report-rail__body--empty");
    expect(within(reportBody).getByText("诊断拓扑信息")).toBeInTheDocument();
    expect(within(reportBody).getByText("候选假设验证")).toBeInTheDocument();
    expect(within(reportBody).getByText("根因结论和修复方案")).toBeInTheDocument();
    expect(within(reportBody).queryByText("诊断摘要")).not.toBeInTheDocument();
    expect(within(reportBody).queryByText("影响范围")).not.toBeInTheDocument();
    expect(within(reportBody).queryByText("候选假设")).not.toBeInTheDocument();
    expect(within(reportBody).queryByText("根因结论")).not.toBeInTheDocument();
    expect(within(reportBody).queryByText("修复建议")).not.toBeInTheDocument();
    expect(within(reportBody).queryByText("执行反馈")).not.toBeInTheDocument();
    expect(within(reportBody).queryByText(/Waiting for/i)).not.toBeInTheDocument();
    expect(within(reportBody).queryByText("生成中")).not.toBeInTheDocument();
    expect(within(reportBody).queryByTestId("diagnosis-modified-report-section-loading")).not.toBeInTheDocument();
  });

  it("keeps all report modules in placeholder state immediately after demo starts", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-preview-transition",
          kind: "message",
          role: "user",
          content: "diagnose auth latency",
          timestamp: "2026-04-08T09:59:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 1200,
          type: "append",
          item: {
            id: "assistant-preview-transition",
            kind: "message",
            role: "assistant",
            content: "demo transition",
            timestamp: "2026-04-08T09:59:01.000Z",
          },
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    renderDemoPage();
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await act(async () => {
      await Promise.resolve();
    });

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    expect(within(reportRail).getByTestId("diagnosis-modified-report-placeholder-context")).toBeInTheDocument();
    expect(within(reportRail).getByTestId("diagnosis-modified-report-placeholder-hypotheses")).toBeInTheDocument();
    expect(within(reportRail).getByTestId("diagnosis-modified-report-placeholder-rootcause")).toBeInTheDocument();
    expect(within(reportRail).getByTestId("diagnosis-modified-report-placeholder-context-progress")).toBeInTheDocument();
    expect(within(reportRail).getByTestId("diagnosis-modified-report-placeholder-hypotheses-progress")).toBeInTheDocument();
    expect(within(reportRail).getByTestId("diagnosis-modified-report-placeholder-rootcause-progress")).toBeInTheDocument();
    expect(
      within(reportRail)
        .getByTestId("diagnosis-modified-report-placeholder-context-progress")
        .querySelectorAll(".diagnosis-modified-report-rail__loading-progress-line").length,
    ).toBe(1);
    expect(
      within(reportRail)
        .getByTestId("diagnosis-modified-report-placeholder-hypotheses-progress")
        .querySelectorAll(".diagnosis-modified-report-rail__loading-progress-line").length,
    ).toBe(1);
    expect(
      within(reportRail)
        .getByTestId("diagnosis-modified-report-placeholder-rootcause-progress")
        .querySelectorAll(".diagnosis-modified-report-rail__loading-progress-line").length,
    ).toBe(1);
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-context")).toHaveAttribute(
      "data-state",
      "placeholder",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-hypotheses")).toHaveAttribute(
      "data-state",
      "placeholder",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-rootcause")).toHaveAttribute(
      "data-state",
      "placeholder",
    );
    expect(within(reportRail).queryByText("用于展示上下游依赖、受影响实体与拓扑关联关系。")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("用于呈现候选根因、验证依据与置信度变化。")).not.toBeInTheDocument();
  });

  it("reveals report modules progressively in demo mode as each section data becomes ready", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-progressive-report",
          kind: "message",
          role: "user",
          content: "diagnose auth latency",
          timestamp: "2026-04-08T09:59:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "demo-tool-topology-progressive",
            kind: "tool",
            toolName: "fetch_topology_context",
            params: {},
            status: "success",
            summaryLines: ["topology parsed"],
            rawResult: {
              roots: ["redis-primary"],
              affected_entities: [{ id: "service:auth-svc", name: "auth-svc" }],
              summary: "topology blast radius: redis-primary impacts auth-svc",
            },
            timestamp: "2026-04-08T09:59:01.000Z",
          },
        },
        {
          delayMs: 1,
          type: "update_candidates",
          candidates: [
            {
              id: "candidate-progressive-demo",
              title: "Redis connection saturation",
              summary: "Redis timeout correlates with auth retries.",
              confidence: 0.78,
              confidenceLabel: "78%",
              statusLabel: "候选 1",
              statusTone: "neutral",
              evidenceFor: ["Redis timeout observed"],
              evidenceAgainst: [],
              entities: ["redis-primary", "auth-svc"],
              rank: 1,
              evidenceSummary: "Redis timeout correlates with auth retries.",
              distinguishingVerification: "Check Redis saturation before scaling rollout.",
              isPrimary: false,
            },
          ],
        },
        {
          delayMs: 2,
          type: "complete",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
      session: createDetailedLiveSession("demo-progressive-report"),
    });

    renderDemoPage();
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await act(async () => {
      await Promise.resolve();
    });

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-context")).toHaveAttribute(
      "data-state",
      "placeholder",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-hypotheses")).toHaveAttribute(
      "data-state",
      "placeholder",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-rootcause")).toHaveAttribute(
      "data-state",
      "placeholder",
    );

    await advance(5);

    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-context")).toHaveAttribute(
      "data-state",
      "placeholder",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-hypotheses")).toHaveAttribute(
      "data-state",
      "ready",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-rootcause")).toHaveAttribute(
      "data-state",
      "placeholder",
    );

    await advance(5);

    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-rootcause")).toHaveAttribute(
      "data-state",
      "ready",
    );
    expect(within(reportRail).queryByTestId("diagnosis-modified-report-placeholder-rootcause")).not.toBeInTheDocument();
    expect(within(reportRail).queryByTestId("diagnosis-modified-report-placeholder-rootcause-progress")).not.toBeInTheDocument();
  });

  it("applies the same progressive reveal strategy for live sessions", async () => {
    let liveViewSource: ReturnType<typeof diagnosisModifiedModel.buildDiagnosisModifiedLiveView> = {
      timeline: [],
      candidates: [],
      summary: undefined,
      plan: undefined,
    };

    mockedBuildLiveView.mockImplementation(() => liveViewSource);

    resetDiagnosisStore({
      session: createLiveSession("sess-live-progressive-report"),
      activeSessionId: "sess-live-progressive-report",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-progressive-report");

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-context")).toHaveAttribute(
      "data-state",
      "ready",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-hypotheses")).toHaveAttribute(
      "data-state",
      "placeholder",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-rootcause")).toHaveAttribute(
      "data-state",
      "placeholder",
    );

    await act(async () => {
      liveViewSource = {
        timeline: [
          {
            id: "live-tool-topology-progressive",
            kind: "tool",
            toolName: "fetch_topology_context",
            params: {},
            status: "success",
            summaryLines: ["topology parsed"],
            rawResult: {
              roots: ["redis-primary"],
              affected_entities: [{ id: "service:auth-svc", name: "auth-svc" }],
              summary: "topology blast radius: redis-primary impacts auth-svc",
            },
            timestamp: "2026-04-08T12:00:01.000Z",
          },
        ],
        candidates: [],
        summary: undefined,
        plan: undefined,
      };

      useDiagnosisStore.setState((state) => ({
        ...state,
        session: { ...(state.session as DiagnosisSession) },
      }));
      await Promise.resolve();
    });

    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-context")).toHaveAttribute(
      "data-state",
      "ready",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-hypotheses")).toHaveAttribute(
      "data-state",
      "placeholder",
    );

    await act(async () => {
      liveViewSource = {
        ...liveViewSource,
        candidates: [
          {
            id: "candidate-progressive-live",
            title: "Redis connection saturation",
            summary: "Redis timeout correlates with auth retries.",
            confidence: 0.83,
            confidenceLabel: "83%",
            statusLabel: "Current candidate",
            statusTone: "accent",
            evidenceFor: ["Redis timeout observed"],
            evidenceAgainst: [],
            entities: ["redis-primary", "auth-svc"],
            rank: 1,
            evidenceSummary: "Redis timeout correlates with auth retries.",
            distinguishingVerification: "Check Redis saturation before scaling rollout.",
            isPrimary: true,
          },
        ],
      };

      useDiagnosisStore.setState((state) => ({
        ...state,
        session: { ...(state.session as DiagnosisSession) },
      }));
      await Promise.resolve();
    });

    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-hypotheses")).toHaveAttribute(
      "data-state",
      "ready",
    );
    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-rootcause")).toHaveAttribute(
      "data-state",
      "placeholder",
    );

    await act(async () => {
      useDiagnosisStore.setState((state) => ({
        ...state,
        session: createDetailedLiveSession("sess-live-progressive-report"),
      }));
      await Promise.resolve();
    });

    expect(within(reportRail).getByTestId("diagnosis-modified-report-progressive-rootcause")).toHaveAttribute(
      "data-state",
      "ready",
    );
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

  it("collapses finished thinking into a unified completion label", async () => {
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

    expect(screen.getByText("Thought completed")).toBeInTheDocument();
    expect(screen.queryByText("Agent is understanding the request")).not.toBeInTheDocument();
  });

  it("keeps active thinking title in the header position without duplicate body copy", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-thinking-duplication",
          kind: "message",
          role: "user",
          content: "demo thinking duplication",
          timestamp: "2026-04-08T10:20:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "thinking-duplication-1",
            kind: "thinking",
            title: "Agent reasoning in progress",
            content: "Collecting metrics and correlating the alert timeline.",
            timestamp: "2026-04-08T10:20:01.000Z",
            status: "thinking",
          },
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    renderDemoPage();
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await advance(120);

    expect(screen.getByRole("heading", { name: "推理中" })).toBeInTheDocument();
    expect(screen.queryByText("Agent reasoning in progress")).not.toBeInTheDocument();
  });

  it("shows only the first 200 chars for completed long thinking content and supports expand", async () => {
    const longContent = `${"A".repeat(200)}TAIL_SEGMENT`;
    const collapsedPreview = `${"A".repeat(200)}...`;
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-thinking-long",
          kind: "message",
          role: "user",
          content: "demo long thinking",
          timestamp: "2026-04-08T10:21:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "thinking-long-1",
            kind: "thinking",
            title: "Long reasoning",
            content: longContent,
            timestamp: "2026-04-08T10:21:01.000Z",
            status: "completed",
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

    expect(screen.getByText("展开全部推理")).toBeInTheDocument();
    expect(screen.getByText(collapsedPreview)).toBeInTheDocument();
    expect(screen.queryByText(longContent)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开全部推理" }));
    expect(screen.getByText("收起推理")).toBeInTheDocument();
    expect(screen.getByText(longContent)).toBeInTheDocument();
  });

  it("renders completed short thinking content collapsed with a details toggle", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-thinking-short",
          kind: "message",
          role: "user",
          content: "demo short thinking",
          timestamp: "2026-04-08T10:22:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "append",
          item: {
            id: "thinking-short-1",
            kind: "thinking",
            title: "Short reasoning",
            content: "short reasoning content",
            timestamp: "2026-04-08T10:22:01.000Z",
            status: "completed",
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

    expect(screen.getByText("short reasoning content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开全部推理" })).toBeInTheDocument();
    expect(screen.queryByText("收起推理")).not.toBeInTheDocument();
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

    await advance(0);

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

    await advance(10);
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

    await advance(0);

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

  it("does not render remediation action buttons in the demo report rail", async () => {
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
    expect(within(reportRail).queryByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" })).not.toBeInTheDocument();
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
          sourceEventType: "remediation_progress",
          sourceEventKey: "remediation_progress:execution_sync:2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    const cases = [
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
          sourceEventType: "remediation_progress",
          sourceEventKey: "remediation_progress:execution_sync:2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createSessionWithStatus("sess-flow-open", "validating"),
      activeSessionId: "sess-flow-open",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-flow-open");

    fireEvent.click(
      within(screen.getByTestId("diagnosis-modified-flow-remediation-entry")).getByRole("button", {
        name: "\u67e5\u770b\u6267\u884c",
      }),
    );

    expect(windowOpenSpy).toHaveBeenCalledWith(
      "/remediation?sessionId=sess-flow-open",
      "_blank",
      "noopener,noreferrer",
    );
  });
  it("does not render remediation action buttons in the live report rail", () => {
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
    expect(within(reportRail).queryByRole("button", { name: "\u5ba1\u6279\u4fee\u590d" })).not.toBeInTheDocument();
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
          sourceEventType: "remediation_progress",
          sourceEventKey: "remediation_progress:execution_sync:2026-04-08T12:00:01.000Z",
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
          sourceEventType: "remediation_progress",
          sourceEventKey: "remediation_progress:execution_sync:2026-04-08T12:00:01.000Z",
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
        {
          id: "live-msg-remediation-progress-1",
          kind: "message",
          role: "assistant",
          content: "灰度批次 1/2 开始。",
          timestamp: "2026-04-08T12:00:09.000Z",
          sourceEventType: "remediation_progress",
          sourceEventKey: "remediation_progress:canary_started:2026-04-08T12:00:09.000Z",
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
    expect(overview).toHaveTextContent("04-08Latency spike诊断报告");
    expect(reportHeader).toHaveTextContent("Auth login latency spikes and partial 5xx responses.");
    expect(reportHeader).toHaveTextContent("Latency spike");
    expect(reportHeader).toHaveTextContent("2026-04-08T12:00:09.000Z");
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
    expect(within(traceList).getByText("推理完成")).toBeInTheDocument();
    expect(within(traceList).getByText("执行工具调用")).toBeInTheDocument();
    expect(within(traceList).getAllByText("形成阶段判断").length).toBeGreaterThan(0);
    expect(within(traceList).getByText("已生成修复建议")).toBeInTheDocument();
    const actionStep = screen.getByTestId("diagnosis-modified-action-generated-step");
    expect(within(actionStep).queryByTestId("diagnosis-modified-flow-remediation-entry")).not.toBeInTheDocument();
    expect(within(actionStep).getByTestId("diagnosis-modified-approval-surface")).toBeInTheDocument();
    expect(actionStep.querySelector(".diagnosis-modified-trace-step__rail")).toBeTruthy();
    expect(container.querySelector(".diagnosis-modified-message-row--user")).toBeNull();
    expect(within(reportRail).getByText("诊断拓扑信息")).toBeInTheDocument();
    expect(within(reportRail).getByText("候选假设验证")).toBeInTheDocument();
    expect(within(reportRail).getByText("根因结论和修复方案")).toBeInTheDocument();
    expect(within(reportRail).queryByText("推理进展")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("影响拓扑")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("诊断进度")).not.toBeInTheDocument();
    expect(within(reportRail).queryByTestId("diagnosis-modified-report-progress")).not.toBeInTheDocument();
    expect(screen.queryByText("归因分析")).not.toBeInTheDocument();
    expect(within(reportRail).queryByTestId("diagnosis-modified-summary-process-status")).not.toBeInTheDocument();
    expect(screen.queryByText("Current Conclusion")).not.toBeInTheDocument();
    expect(screen.queryByText("Root Cause Assessment")).not.toBeInTheDocument();
    expect(screen.queryByText("归因分析")).not.toBeInTheDocument();
    expect(screen.queryByText("关键证据")).toBeNull();
    expect(screen.queryByText("修复建议")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/^01$/)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/^02$/)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/^03$/)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/状态:\s*当前根因/u)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/关联实体:/u)).not.toBeInTheDocument();
    expect(container.querySelector(".diagnosis-modified-report-rail__context-graph")).toBeTruthy();
    expect(container.querySelector(".diagnosis-modified-report-rail__context-lists")).toBeNull();
    expect(container.querySelectorAll(".diagnosis-modified-report-rail__section").length).toBe(3);
    expect(within(reportRail).getAllByText("RANK #1：Redis connection saturation").length).toBeGreaterThan(0);
    expect(
      within(reportRail).getAllByText("Throttle rollout and validate Redis recovery before expanding.").length,
    ).toBeGreaterThan(0);
    expect(within(reportRail).queryByText(/^Rank$/)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("Layer")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("Entities")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("Confidence")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/^根因$/)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/^层级$/)).not.toBeInTheDocument();
    expect(within(reportRail).queryByText(/^实体$/)).not.toBeInTheDocument();
    expect(within(reportRail).getAllByText(/置信度/).length).toBeGreaterThan(0);
    expect(within(reportRail).queryByText("Execute 10% canary first, then observe Redis timeout recovery.")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("Hypothesis summary")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("结论已趋于稳定，默认折叠细节；可按候选展开查看证据与置信度变化。")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("已选中 3 个候选假设。")).not.toBeInTheDocument();
    expect(within(reportRail).getByText("Redis timeout and retry loop align with alert timing.")).toBeInTheDocument();
    expect(within(reportRail).queryByText("仅展示告警主体与其直连关联实体；当缺少直连关系时，仅展示主体节点并给出提示。")).not.toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-report-rail-loading")).not.toBeInTheDocument();
  });

  it("keeps live timeline source order when ids are reverse-lexicographic under the same timestamp", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "z-live-thinking-same-ts",
          kind: "thinking",
          title: "First in source order",
          content: "First by source order",
          timestamp: "2026-04-08T12:20:00.000Z",
          status: "completed",
        },
        {
          id: "a-live-message-same-ts",
          kind: "message",
          role: "assistant",
          content: "Second by source order",
          timestamp: "2026-04-08T12:20:00.000Z",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-source-order"),
      activeSessionId: "sess-live-source-order",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-source-order");

    const traceList = screen.getByTestId("diagnosis-modified-trace-list");
    const firstNode = within(traceList).getByText("First by source order");
    const secondNode = within(traceList).getByText("Second by source order");
    expect(firstNode.compareDocumentPosition(secondNode) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("merges streaming and completed thinking for the same thought_key into a single visible card", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "trace-thinking-same-round",
          kind: "thinking",
          title: "Agent is converging on the diagnosis",
          content: "已完成推理内容",
          timestamp: "2026-04-08T12:21:00.000Z",
          status: "completed",
          thoughtKey: "run-reason-1:reason",
          phase: "completed",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-thinking-merge"),
      activeSessionId: "sess-live-thinking-merge",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      liveThinking: {
        round_id: "run-reason-1@2026-04-08T12:21:05.000Z",
        round_seq: 1,
        thought_key: "run-reason-1:reason",
        run_id: "run-reason-1",
        node: "reason",
        timestamp: "2026-04-08T12:21:05.000Z",
        content: "推理中最新输出",
        status: "thinking",
        stream_seq: 2,
        thought_duration_sec: null,
        next_action: null,
        tool_name: null,
        active_tools: [],
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-thinking-merge");

    expect(screen.getByText("推理中")).toBeInTheDocument();
    expect(screen.queryByText("推理完成")).not.toBeInTheDocument();
    expect(screen.getByText("推理中最新输出")).toBeInTheDocument();
  });

  it("replaces stale streaming thinking in-place when the same round completes", async () => {
    let liveViewSource: ReturnType<typeof diagnosisModifiedModel.buildDiagnosisModifiedLiveView> = {
      timeline: [
        {
          id: "live-msg-before-replace",
          kind: "message",
          role: "assistant",
          content: "Before replace marker",
          timestamp: "2026-04-08T12:22:00.000Z",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    };
    mockedBuildLiveView.mockImplementation(() => liveViewSource);

    resetDiagnosisStore({
      session: createLiveSession("sess-live-thinking-replace"),
      activeSessionId: "sess-live-thinking-replace",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      liveThinking: {
        round_id: "run-reason-2@2026-04-08T12:22:05.000Z",
        round_seq: 2,
        thought_key: "run-reason-2:reason",
        run_id: "run-reason-2",
        node: "reason",
        timestamp: "2026-04-08T12:22:05.000Z",
        content: "推理中间态内容",
        status: "thinking",
        stream_seq: 3,
        thought_duration_sec: null,
        next_action: null,
        tool_name: null,
        active_tools: [],
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-thinking-replace");
    expect(screen.getByText("推理中")).toBeInTheDocument();

    liveViewSource = {
      timeline: [
        {
          id: "live-msg-before-replace",
          kind: "message",
          role: "assistant",
          content: "Before replace marker",
          timestamp: "2026-04-08T12:22:00.000Z",
        },
        {
          id: "trace-thinking-run-reason-2:reason",
          kind: "thinking",
          title: "Agent is converging on the diagnosis",
          content: "推理完成最终内容",
          timestamp: "2026-04-08T12:22:06.000Z",
          status: "completed",
          thoughtKey: "run-reason-2:reason",
          phase: "completed",
        },
        {
          id: "live-msg-after-replace",
          kind: "message",
          role: "assistant",
          content: "After replace marker",
          timestamp: "2026-04-08T12:22:07.000Z",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    };

    await act(async () => {
      useDiagnosisStore.setState({
        liveThinking: null,
        messages: [{ id: "msg-trigger-rerender", role: "assistant", content: "trigger" }],
      });
      await Promise.resolve();
    });

    expect(screen.queryByText("推理中")).not.toBeInTheDocument();
    expect(screen.getAllByText("推理完成")).toHaveLength(1);
    expect(screen.getByText("推理完成最终内容")).toBeInTheDocument();

    const beforeNode = screen.getByText("Before replace marker");
    const completedNode = screen.getByText("推理完成最终内容");
    expect(beforeNode.compareDocumentPosition(completedNode) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows only the latest semantic fragment for long streaming thinking text", () => {
    resetDiagnosisStore({
      session: createLiveSession("sess-live-thinking-fragment"),
      activeSessionId: "sess-live-thinking-fragment",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      liveThinking: {
        round_id: "run-fragment@2026-04-08T12:23:05.000Z",
        round_seq: 1,
        thought_key: "run-fragment:reason",
        run_id: "run-fragment",
        node: "reason",
        timestamp: "2026-04-08T12:23:05.000Z",
        content:
          "第一步检查 GPU 指标与进程状态，确认服务节点异常。第二步排除外部压测干扰。现在需要生成 remediation_plan 部分。",
        status: "thinking",
        stream_seq: 1,
        thought_duration_sec: null,
        next_action: null,
        tool_name: null,
        active_tools: [],
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-thinking-fragment");

    expect(screen.getByText("推理中")).toBeInTheDocument();
    expect(screen.getByText("现在需要生成 remediation_plan 部分")).toBeInTheDocument();
    expect(screen.queryByText(/第一步检查 GPU 指标与进程状态/)).not.toBeInTheDocument();
  });

  it("falls back to a tail preview for long structured streaming content", () => {
    const longStructuredLine =
      'query=Diagnose the operational issue described by the alert below using a read-only workflow, keep collecting evidence, remediation_plan={"step":"kill","target":"fi_gpu_burn_gpu_cont","node":"worker-03","decision":"prepare approval gate before execution"}';
    const expectedTail = `...${longStructuredLine.slice(-80)}`;

    resetDiagnosisStore({
      session: createLiveSession("sess-live-thinking-tail"),
      activeSessionId: "sess-live-thinking-tail",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      liveThinking: {
        round_id: "run-tail@2026-04-08T12:24:05.000Z",
        round_seq: 1,
        thought_key: "run-tail:reason",
        run_id: "run-tail",
        node: "reason",
        timestamp: "2026-04-08T12:24:05.000Z",
        content: longStructuredLine,
        status: "thinking",
        stream_seq: 1,
        thought_duration_sec: null,
        next_action: null,
        tool_name: null,
        active_tools: [],
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-thinking-tail");

    expect(screen.getByText(expectedTail)).toBeInTheDocument();
    expect(screen.queryByText(longStructuredLine)).not.toBeInTheDocument();
  });

  it("does not keep stale thinking from a previous round when a new round starts streaming", async () => {
    let liveViewSource: ReturnType<typeof diagnosisModifiedModel.buildDiagnosisModifiedLiveView> = {
      timeline: [],
      candidates: [],
      summary: undefined,
      plan: undefined,
    };
    mockedBuildLiveView.mockImplementation(() => liveViewSource);

    resetDiagnosisStore({
      session: createLiveSession("sess-live-round-cleanup"),
      activeSessionId: "sess-live-round-cleanup",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      liveThinking: {
        round_id: "run-round-a@2026-04-08T12:28:01.000Z",
        round_seq: 1,
        thought_key: "run-round-a:reason",
        run_id: "run-round-a",
        node: "reason",
        timestamp: "2026-04-08T12:28:01.000Z",
        content: "上一轮推理中内容",
        status: "thinking",
        stream_seq: 1,
        thought_duration_sec: null,
        next_action: null,
        tool_name: null,
        active_tools: [],
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-round-cleanup");
    expect(screen.getByText("上一轮推理中内容")).toBeInTheDocument();

    liveViewSource = {
      timeline: [
        {
          id: "trace-thinking-old-round-without-key",
          kind: "thinking",
          title: "Agent is converging on the diagnosis",
          content: "上一轮推理完成内容",
          timestamp: "2026-04-08T12:28:03.000Z",
          status: "completed",
          phase: "completed",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    };

    await act(async () => {
      useDiagnosisStore.setState({
        liveThinking: {
          round_id: "run-round-b@2026-04-08T12:28:04.000Z",
          round_seq: 2,
          thought_key: "run-round-b:reason",
          run_id: "run-round-b",
          node: "reason",
          timestamp: "2026-04-08T12:28:04.000Z",
          content: "新一轮推理中内容",
          status: "thinking",
          stream_seq: 1,
          thought_duration_sec: null,
          next_action: null,
          tool_name: null,
          active_tools: [],
        },
        messages: [{ id: "msg-round-cleanup-trigger", role: "assistant", content: "trigger" }],
      });
      await Promise.resolve();
    });

    expect(screen.queryByText("上一轮推理中内容")).not.toBeInTheDocument();
    expect(screen.getByText("新一轮推理中内容")).toBeInTheDocument();
  });

  it("renders completed thinking collapsed by default and expands on demand", () => {
    const longThinking = "第一段推理。".repeat(80);
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "trace-thinking-collapsed",
          kind: "thinking",
          title: "Agent is converging on the diagnosis",
          content: longThinking,
          timestamp: "2026-04-08T12:25:00.000Z",
          status: "completed",
          thoughtKey: "run-reason-2:reason",
          phase: "completed",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-thinking-collapsed"),
      activeSessionId: "sess-live-thinking-collapsed",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-thinking-collapsed");

    const expandButton = screen.getByRole("button", { name: "展开全部推理" });
    expect(expandButton).toBeInTheDocument();

    fireEvent.click(expandButton);
    expect(screen.getByRole("button", { name: "收起推理" })).toBeInTheDocument();
  });

  it("uses smart auto-follow and does not force-scroll when user has scrolled away from bottom", async () => {
    let liveViewSource: ReturnType<typeof diagnosisModifiedModel.buildDiagnosisModifiedLiveView> = {
      timeline: [
        {
          id: "live-msg-scroll-1",
          kind: "message",
          role: "assistant",
          content: "First timeline item",
          timestamp: "2026-04-08T12:30:01.000Z",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    };

    mockedBuildLiveView.mockImplementation(() => liveViewSource);
    resetDiagnosisStore({
      session: createLiveSession("sess-live-smart-follow"),
      activeSessionId: "sess-live-smart-follow",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const scrollIntoViewSpy = vi.spyOn(Element.prototype, "scrollIntoView");
    renderLivePage("/diagnosis-modified/sess-live-smart-follow");
    const timelineFeed = screen.getByTestId("diagnosis-modified-timeline");

    let scrollTopValue = 700;
    Object.defineProperty(timelineFeed, "scrollTop", {
      configurable: true,
      get: () => scrollTopValue,
      set: (value: number) => {
        scrollTopValue = value;
      },
    });
    Object.defineProperty(timelineFeed, "clientHeight", {
      configurable: true,
      get: () => 300,
    });
    Object.defineProperty(timelineFeed, "scrollHeight", {
      configurable: true,
      get: () => 1000,
    });

    await act(async () => {
      await Promise.resolve();
    });
    scrollIntoViewSpy.mockClear();

    scrollTopValue = 200;
    fireEvent.scroll(timelineFeed);

    await act(async () => {
      liveViewSource = {
        ...liveViewSource,
        timeline: [
          ...liveViewSource.timeline,
          {
            id: "live-msg-scroll-2",
            kind: "message",
            role: "assistant",
            content: "Second timeline item",
            timestamp: "2026-04-08T12:30:02.000Z",
          },
        ],
      };
      useDiagnosisStore.setState((state) => ({
        ...state,
        session: { ...(state.session as DiagnosisSession) },
      }));
      await Promise.resolve();
    });

    expect(scrollIntoViewSpy).not.toHaveBeenCalled();

    scrollTopValue = 705;
    fireEvent.scroll(timelineFeed);

    await act(async () => {
      liveViewSource = {
        ...liveViewSource,
        timeline: [
          ...liveViewSource.timeline,
          {
            id: "live-msg-scroll-3",
            kind: "message",
            role: "assistant",
            content: "Third timeline item",
            timestamp: "2026-04-08T12:30:03.000Z",
          },
        ],
      };
      useDiagnosisStore.setState((state) => ({
        ...state,
        session: { ...(state.session as DiagnosisSession) },
      }));
      await Promise.resolve();
    });

    expect(scrollIntoViewSpy).toHaveBeenCalled();
  });

  it("keeps only one next-action entry when source id rotates across refreshes", async () => {
    let liveViewSource: ReturnType<typeof diagnosisModifiedModel.buildDiagnosisModifiedLiveView> = {
      timeline: [
        {
          id: "diagnosis-result-next-action-2026-04-22T14:29:00.000Z",
          kind: "message",
          role: "assistant",
          label: "Next action",
          content: "批准kill_process提案，终止fi_gpu_burn_gpu_cont进程以释放GPU资源",
          timestamp: "2026-04-22T14:29:00.000Z",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    };

    mockedBuildLiveView.mockImplementation(() => liveViewSource);
    resetDiagnosisStore({
      session: createLiveSession("sess-live-next-action-dedupe"),
      activeSessionId: "sess-live-next-action-dedupe",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-next-action-dedupe");
    const traceList = screen.getByTestId("diagnosis-modified-trace-list");
    expect(within(traceList).getAllByText("Next action")).toHaveLength(1);

    await act(async () => {
      liveViewSource = {
        ...liveViewSource,
        timeline: [
          {
            id: "diagnosis-result-next-action-2026-04-22T14:29:09.000Z",
            kind: "message",
            role: "assistant",
            label: "Next action",
            content: "批准kill_process提案，终止fi_gpu_burn_gpu_cont进程以释放GPU资源",
            timestamp: "2026-04-22T14:29:09.000Z",
          },
        ],
      };
      useDiagnosisStore.setState((state) => ({
        ...state,
        session: { ...(state.session as DiagnosisSession) },
      }));
      await Promise.resolve();
    });

    expect(within(traceList).getAllByText("Next action")).toHaveLength(1);
  });

  it("renders impact topology from topology_context before diagnosis_result is ready", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-msg-topology-context",
          kind: "message",
          role: "assistant",
          content:
            '[HumanMessage - topology_context]\nTopology context:\n{"roots":["wj-lab-cpt-01"],"affected_count":2,"affected_entities":[{"id":"gpu:0","name":"GPU 0"},{"id":"service:auth-svc","name":"auth-svc"}],"summary":"topology blast radius: roots=[\'wj-lab-cpt-01\'], affected_count=2"}',
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-topology-context"),
      activeSessionId: "sess-live-topology-context",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      topologyContext: {
        roots: ["gpu:0"],
        affected_count: 2,
        affected_entities: [
          { id: "node:wj-lab-cpt-01", type: "node", name: "wj-lab-cpt-01" },
          { id: "bmc:wj-lab-cpt-01-bmc", type: "bmc", name: "wj-lab-cpt-01-bmc" },
        ],
        summary: "GPU context from diagnosis_started",
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis-modified/sess-live-topology-context");

    expect(container.querySelector(".diagnosis-modified-report-rail__context-graph")).toBeTruthy();
    expect(within(screen.getByTestId("diagnosis-modified-report-rail")).queryByText("影响拓扑")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("diagnosis-modified-report-rail")).queryByTestId("diagnosis-modified-report-progress")).not.toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("wj-lab-cpt-01")).toBeInTheDocument();
  });

  it("keeps diagnosis context graph hover tooltip behavior unchanged", async () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-topology-tooltip"),
      activeSessionId: "sess-live-topology-tooltip",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      topologyContext: {
        roots: ["gpu:0"],
        affected_count: 2,
        affected_entities: [
          { id: "node:wj-lab-cpt-01", type: "node", name: "wj-lab-cpt-01" },
          { id: "bmc:wj-lab-cpt-01-bmc", type: "bmc", name: "wj-lab-cpt-01-bmc" },
        ],
        summary: "GPU context from diagnosis_started",
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    const { container } = renderLivePage("/diagnosis-modified/sess-live-topology-tooltip");
    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    const nodeButton = within(reportRail)
      .getAllByRole("button")
      .find((button) => /\|/.test(button.getAttribute("aria-label") ?? ""));

    expect(nodeButton).toBeTruthy();
    fireEvent.mouseEnter(nodeButton as HTMLButtonElement);

    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector(".topology-modified-tooltip")).toBeTruthy();
    expect(container.querySelector("[data-testid=\"diagnosis-context-node-popover\"]")).toBeNull();
  });

  it("shows alert subject only when no direct topology relation is available", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-topology-empty-direct"),
      activeSessionId: "sess-live-topology-empty-direct",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      topologyContext: {
        roots: [],
        affected_count: 0,
        affected_entities: [],
        summary: "no linked entities",
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-topology-empty-direct");

    expect(screen.getByText("auth-svc")).toBeInTheDocument();
    expect(screen.queryByText("暂无直连关联实体，当前仅展示告警主体。")).not.toBeInTheDocument();
  });

  it("renders diagnosis-start context as a single live thinking card", async () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-streaming-context"),
      activeSessionId: "sess-live-streaming-context",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      events: [],
      alertSnapshot: {
        alert_name: "GPUTemperatureHigh",
        severity: "critical",
        labels: { node: "worker-03" },
        starts_at: "2026-04-08T12:00:00.000Z",
      } as never,
      topologyContext: {
        roots: ["gpu:0"],
        affected_count: 2,
        affected_entities: [
          { id: "node:worker-03", type: "node", name: "worker-03" },
          { id: "bmc:worker-03-bmc", type: "bmc", name: "worker-03-bmc" },
        ],
        summary: "gpu -> node -> bmc",
      },
      liveThinking: {
        round_id: "round-live-1",
        round_seq: 1,
        thought_key: "run:reason",
        run_id: "run-1",
        node: "reason",
        timestamp: "2026-04-08T12:00:02.000Z",
        content: "正在检查 GPU 温度告警的上下文与拓扑链路。",
        status: "thinking",
        stream_seq: 2,
        thought_duration_sec: null,
        next_action: null,
        tool_name: "ssh.run_command",
        active_tools: [],
      } as never,
      activeStreamingTools: [
        {
          tool: "ssh.run_command",
          params: { node: "10.11.4.12" },
          round_id: "round-live-1",
          round_seq: 1,
          thought_key: "run:reason",
          run_id: "run-1",
          node: "reason",
        },
      ],
      liveFinalAnswer: {
        id: "live-final-1",
        timestamp: "2026-04-08T12:00:03.000Z",
        content: "建议先检查风扇策略与机柜散热。",
        status: "streaming",
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-streaming-context");

    expect(screen.getByRole("heading", { name: "正在构建上下文..." })).toBeInTheDocument();
    expect(screen.queryByText("诊断开始上下文")).not.toBeInTheDocument();
    await advance(5000);
    expect(screen.getByText("拓扑摘要 gpu -> node -> bmc")).toBeInTheDocument();
    expect(screen.queryByText("正在检查 GPU 温度告警的上下文与拓扑链路。")).not.toBeInTheDocument();
    expect(screen.getAllByText("ssh.run_command").length).toBeGreaterThan(0);
    expect(screen.getByText("建议先检查风扇策略与机柜散热。")).toBeInTheDocument();
  });

  it("switches context card from building to completed after first node_completed", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-streaming-context-completed"),
      activeSessionId: "sess-live-streaming-context-completed",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      events: [
        {
          schema_version: "1",
          type: "node_completed",
          session_id: "sess-live-streaming-context-completed",
          timestamp: "2026-04-08T12:00:05.000Z",
          data: {
            node: "reason",
            status: "diagnosing",
          },
        } as never,
      ],
      alertSnapshot: {
        alert_name: "GPUTemperatureHigh",
        severity: "critical",
        labels: { node: "worker-03" },
        starts_at: "2026-04-08T12:00:00.000Z",
      } as never,
      topologyContext: {
        roots: ["gpu:0"],
        affected_count: 2,
        affected_entities: [],
        summary: "gpu -> node -> bmc",
      },
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-streaming-context-completed");

    expect(screen.getByRole("heading", { name: "推理完成" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "正在构建上下文..." })).not.toBeInTheDocument();
  });

  it("does not show action-generated step before remediation stage is ready", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-message-before-remediation",
          kind: "message",
          role: "assistant",
          content: "仍在收敛候选根因。",
          timestamp: "2026-04-08T12:01:00.000Z",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    });

    const session = createLiveSession("sess-live-before-remediation");
    session.status = "approved";
    session.diagnosis_result = null;

    resetDiagnosisStore({
      session,
      activeSessionId: "sess-live-before-remediation",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-before-remediation");

    expect(screen.queryByTestId("diagnosis-modified-action-generated-step")).not.toBeInTheDocument();
  });

  it("inserts action-generated step before the first remediation agent response", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-next-action-seq",
          kind: "message",
          role: "assistant",
          label: "Next action",
          content: "先审批并开始修复。",
          timestamp: "2026-04-08T12:10:00.000Z",
        },
        {
          id: "live-msg-remediation-seq-1",
          kind: "message",
          role: "assistant",
          content: "审批通过，准备执行修复",
          timestamp: "2026-04-08T12:10:01.000Z",
          sourceEventType: "remediation_progress",
          sourceEventKey: "remediation_progress:execution_sync:2026-04-08T12:10:01.000Z",
        },
        {
          id: "live-msg-remediation-seq-2",
          kind: "message",
          role: "assistant",
          content: "已采集修复前基线",
          timestamp: "2026-04-08T12:10:02.000Z",
          sourceEventType: "remediation_progress",
          sourceEventKey: "remediation_progress:baseline_collected:2026-04-08T12:10:02.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createDetailedLiveSession("sess-live-action-order"),
      activeSessionId: "sess-live-action-order",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-action-order");

    const traceList = screen.getByTestId("diagnosis-modified-trace-list");
    const nextActionHeading = within(traceList).getByText("Next action");
    const actionStepHeading = within(traceList).getByText("已生成修复建议");
    const remediationResponse = within(traceList).getByText("审批通过，准备执行修复");

    expect(within(traceList).getAllByText("已生成修复建议")).toHaveLength(1);
    expect(nextActionHeading.compareDocumentPosition(actionStepHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(actionStepHeading.compareDocumentPosition(remediationResponse) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders action-generated step after next-action when remediation responses are not present yet", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-next-action-no-remediation-msg",
          kind: "message",
          role: "assistant",
          label: "Next action",
          content: "建议先审批修复动作。",
          timestamp: "2026-04-08T12:20:00.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createDetailedLiveSession("sess-live-action-hidden"),
      activeSessionId: "sess-live-action-hidden",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-action-hidden");

    const traceList = screen.getByTestId("diagnosis-modified-trace-list");
    const nextActionHeading = within(traceList).getByText("Next action");
    const actionStepHeading = within(traceList).getByText("已生成修复建议");

    expect(nextActionHeading.compareDocumentPosition(actionStepHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders action-generated step for proposal-only approval flows without next-action or remediation messages", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-msg-proposal-only",
          kind: "message",
          role: "assistant",
          content: "已基于主根因补全 proposal-only 修复方案，等待人工审批。",
          timestamp: "2026-04-08T12:21:00.000Z",
        },
      ],
      candidates: [],
      summary: baseSummary,
      plan: basePlan,
    });

    const proposalOnlySession = createDetailedLiveSession("sess-live-proposal-only");
    proposalOnlySession.status = "approval_required";

    resetDiagnosisStore({
      session: proposalOnlySession,
      activeSessionId: "sess-live-proposal-only",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-proposal-only");

    const traceList = screen.getByTestId("diagnosis-modified-trace-list");
    expect(within(traceList).getByText("已生成修复建议")).toBeInTheDocument();
    expect(within(traceList).getByTestId("diagnosis-modified-approval-surface")).toBeInTheDocument();
  });

  it("keeps the trace header stage synchronized without rendering a right-side progress summary card", async () => {
    let liveViewSource: ReturnType<typeof diagnosisModifiedModel.buildDiagnosisModifiedLiveView> = {
      timeline: [
        {
          id: "live-msg-topology-only",
          kind: "message",
          role: "assistant",
          content:
            '[HumanMessage - topology_context]\nTopology context:\n{"roots":["redis-primary"],"affected_count":1,"affected_entities":[{"id":"service:auth-svc","name":"auth-svc"}],"summary":"topology blast radius: redis-primary impacts auth-svc"}',
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [],
      summary: undefined,
      plan: undefined,
    };

    mockedBuildLiveView.mockImplementation(() => liveViewSource);

    resetDiagnosisStore({
      session: createLiveSession("sess-live-progress-sync"),
      activeSessionId: "sess-live-progress-sync",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-progress-sync");

    expect(screen.queryByTestId("diagnosis-modified-trace-sync-stage")).not.toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-report-progress")).not.toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-summary-process-status")).not.toBeInTheDocument();

    await act(async () => {
      liveViewSource = {
        timeline: [
          ...liveViewSource.timeline,
          {
            id: "live-tool-progress-sync",
            kind: "tool",
            toolName: "query_service_metrics",
            params: { service: "auth-svc" },
            timestamp: "2026-04-08T12:02:00.000Z",
            status: "success",
            summaryLines: ["redis_timeout: +240%", "retry_rate: +180%"],
            rawResult: { redis_timeout: "240%", retry_rate: "180%" },
          },
          {
            id: "live-msg-progress-next",
            kind: "message",
            role: "assistant",
            content: "Next action: validate Redis saturation against retry amplification before rollout.",
            timestamp: "2026-04-08T12:02:30.000Z",
            label: "Next action",
          },
        ],
        candidates: [
          {
            id: "candidate-progress-redis",
            title: "Redis connection saturation",
            summary: "Redis timeout and retry amplification align with the alert window.",
            confidence: 0.84,
            confidenceLabel: "84%",
            statusLabel: "Current candidate",
            statusTone: "accent",
            evidenceFor: ["Redis timeout observed"],
            evidenceAgainst: [],
            entities: ["redis-primary", "auth-svc"],
            rank: 1,
            evidenceSummary: "Redis timeout and retry amplification align with the alert window.",
            isPrimary: true,
          },
        ],
        summary: undefined,
        plan: undefined,
      };

      useDiagnosisStore.setState((state) => ({
        ...state,
        session: { ...(state.session as DiagnosisSession) },
      }));
      await Promise.resolve();
    });

    expect(screen.queryByTestId("diagnosis-modified-trace-sync-stage")).not.toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-report-progress")).not.toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-summary-process-status")).not.toBeInTheDocument();

    await act(async () => {
      liveViewSource = {
        ...liveViewSource,
        summary: baseSummary,
        plan: basePlan,
      };

      useDiagnosisStore.setState((state) => ({
        ...state,
        session: createDetailedLiveSession("sess-live-progress-sync"),
      }));
      await Promise.resolve();
    });

    expect(screen.queryByTestId("diagnosis-modified-trace-sync-stage")).not.toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-report-progress")).not.toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-summary-process-status")).not.toBeInTheDocument();

    await act(async () => {
      useDiagnosisStore.setState((state) => ({
        ...state,
        session: createSessionWithStatus("sess-live-progress-sync", "resolved"),
      }));
      await Promise.resolve();
    });

    expect(screen.queryByTestId("diagnosis-modified-summary-process-status")).not.toBeInTheDocument();
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
    expect(within(reportRail).getByText("诊断拓扑信息")).toBeInTheDocument();
    expect(within(reportRail).getByText("候选假设验证")).toBeInTheDocument();
    expect(within(reportRail).getByText("根因结论和修复方案")).toBeInTheDocument();
    expect(within(reportRail).queryByText("推理进展")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("影响拓扑")).not.toBeInTheDocument();
    expect(within(reportRail).queryByText("诊断进度")).not.toBeInTheDocument();
    expect(within(reportRail).queryByTestId("diagnosis-modified-report-progress")).not.toBeInTheDocument();
    expect(screen.queryByText("Current Conclusion")).not.toBeInTheDocument();
    expect(screen.queryByText("Root Cause Assessment")).not.toBeInTheDocument();
    expect(screen.queryByText("关键证据")).toBeNull();
    expect(screen.queryByText("修复建议")).not.toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-modified-report-rail-loading")).not.toBeInTheDocument();
    expect(within(reportRail).queryByTestId("diagnosis-modified-report-section-loading")).not.toBeInTheDocument();
    expect(within(reportRail).getByTestId("diagnosis-modified-report-placeholder-rootcause")).toBeInTheDocument();
  });

  it("shows per-hypothesis evidence and confidence details during demo convergence and keeps the settled view available", async () => {
    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-hypothesis-detail",
          kind: "message",
          role: "user",
          content: "diagnose auth latency",
          timestamp: "2026-04-08T12:10:00.000Z",
        },
      ],
      events: [
        {
          delayMs: 0,
          type: "update_candidates",
          candidates: [
            {
              id: "candidate-redis-demo-initial",
              title: "Redis connection saturation",
              summary: "Redis timeout is the first strong signal.",
              confidence: 0.46,
              confidenceLabel: "46%",
              statusLabel: "候选 1",
              statusTone: "neutral",
              evidenceFor: ["Redis timeout observed"],
              evidenceAgainst: [],
              entities: ["redis-primary", "auth-svc"],
              rank: 1,
              evidenceSummary: "Redis timeout correlates with the alert window.",
              distinguishingVerification: "Check Redis saturation before scaling rollout.",
              isPrimary: false,
            },
          ],
        },
        {
          delayMs: 1,
          type: "update_candidates",
          candidates: [
            {
              id: "candidate-redis-demo-final",
              title: "Redis connection saturation",
              summary: "Redis timeout and retry amplification now dominate the evidence.",
              confidence: 0.86,
              confidenceLabel: "86%",
              statusLabel: "当前根因",
              statusTone: "accent",
              evidenceFor: ["Redis timeout observed", "Retry amplification confirmed"],
              evidenceAgainst: [],
              entities: ["redis-primary", "auth-svc"],
              rank: 1,
              evidenceSummary: "Redis timeout and retry amplification dominate the evidence.",
              distinguishingVerification: "Check Redis saturation before scaling rollout.",
              isPrimary: true,
            },
          ],
        },
        {
          delayMs: 2,
          type: "complete",
        },
      ],
      candidates: [
        {
          id: "candidate-redis-demo-final",
          title: "Redis connection saturation",
          summary: "Redis timeout and retry amplification now dominate the evidence.",
          confidence: 0.86,
          confidenceLabel: "86%",
          statusLabel: "当前根因",
          statusTone: "accent",
          evidenceFor: ["Redis timeout observed", "Retry amplification confirmed"],
          evidenceAgainst: [],
          entities: ["redis-primary", "auth-svc"],
          rank: 1,
          evidenceSummary: "Redis timeout and retry amplification dominate the evidence.",
          distinguishingVerification: "Check Redis saturation before scaling rollout.",
          isPrimary: true,
        },
      ],
      summary: baseSummary,
      plan: basePlan,
      session: createDetailedLiveSession("demo-session-hypothesis-detail"),
    });

    renderDemoPage();
    fireEvent.click(screen.getByRole("button", { name: /Start Demo/i }));

    await advance(200);

    const reportRail = screen.getByTestId("diagnosis-modified-report-rail");
    const hypothesisCard = within(reportRail).getByTestId("diagnosis-modified-hypothesis-card-candidate-redis-demo-initial");
    expect(within(hypothesisCard).getAllByText("支持证据").length).toBeGreaterThan(0);
    expect(within(hypothesisCard).queryByText("Confidence updates")).not.toBeInTheDocument();
    expect(within(hypothesisCard).getByText("Redis timeout observed")).toBeInTheDocument();
    expect(within(hypothesisCard).getByText("46%")).toBeInTheDocument();
    expect(within(hypothesisCard).getByText("有可能")).toHaveClass("diagnosis-modified-badge--warning");
    expect(within(hypothesisCard).getByRole("button", { name: "收起证据链" })).toBeInTheDocument();
    expect(within(hypothesisCard).queryByText("Primary root cause")).not.toBeInTheDocument();
    expect(within(hypothesisCard).queryByText("Candidate root cause")).not.toBeInTheDocument();
    expect(within(hypothesisCard).queryByRole("button", { name: "Expand details" })).not.toBeInTheDocument();

    await flushPendingTimers();

    const settledCard = within(reportRail).getByTestId("diagnosis-modified-hypothesis-card-candidate-redis-demo-final");
    const settledToggle = within(settledCard).queryByRole("button", { name: "展开证据链" });
    if (settledToggle) {
      fireEvent.click(settledToggle);
    }
    const settledDetails = within(settledCard).getByTestId("diagnosis-modified-hypothesis-details-candidate-redis-demo-final");
    expect(within(settledDetails).getAllByText("支持证据").length).toBeGreaterThan(0);
    expect(within(settledDetails).queryByText("Confidence updates")).not.toBeInTheDocument();
    expect(within(settledCard).getByText("86%")).toBeInTheDocument();
    expect(within(settledCard).getByText("已确认")).toHaveClass("diagnosis-modified-badge--success");
    expect(within(settledCard).queryByText("Primary root cause")).not.toBeInTheDocument();
    expect(within(settledCard).queryByText("Candidate root cause")).not.toBeInTheDocument();
  });
});

describe("DiagnosisModifiedPage hypothesis detail toggles after settlement", () => {
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

  it("keeps hypothesis details collapsed after root cause settles but allows per-card expansion", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "live-msg-hypothesis-settled",
          kind: "message",
          role: "assistant",
          content: "Settled report ready.",
          timestamp: "2026-04-08T12:00:01.000Z",
        },
      ],
      candidates: [
        {
          id: "candidate-settled-1",
          title: "Redis connection saturation",
          summary: "Redis timeout correlates with auth retry amplification.",
          confidence: 0.86,
          confidenceLabel: "86%",
          statusLabel: "当前根因",
          statusTone: "accent",
          evidenceFor: ["Redis timeout observed"],
          evidenceAgainst: [],
          entities: ["redis-primary", "auth-svc"],
          rank: 1,
          evidenceSummary: "Redis timeout and retry loop align with alert timing.",
          distinguishingVerification: "Check Redis saturation before scaling rollout.",
          isPrimary: true,
        },
      ],
      summary: baseSummary,
      plan: basePlan,
    });

    resetDiagnosisStore({
      session: createDetailedLiveSession("sess-live-hypothesis-settled"),
      activeSessionId: "sess-live-hypothesis-settled",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis-modified/sess-live-hypothesis-settled");

    expect(screen.queryByText("支持证据")).not.toBeInTheDocument();
    const settledCard = screen.getByTestId("diagnosis-modified-hypothesis-card-candidate-settled-1");
    expect(within(settledCard).getByText("86%")).toBeInTheDocument();
    expect(within(settledCard).getByText("已确认")).toHaveClass("diagnosis-modified-badge--success");
    const toggle = screen.getByRole("button", { name: "展开证据链" });
    fireEvent.click(toggle);
    const detailPanel = screen.getByTestId("diagnosis-modified-hypothesis-details-candidate-settled-1");
    expect(detailPanel).toBeInTheDocument();
    expect(within(detailPanel).getAllByText("支持证据").length).toBeGreaterThan(0);
    expect(within(detailPanel).getByText("Redis timeout observed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起证据链" })).toBeInTheDocument();
    expect(screen.queryByText("Primary root cause")).not.toBeInTheDocument();
    expect(screen.queryByText("Candidate root cause")).not.toBeInTheDocument();
  });
});
