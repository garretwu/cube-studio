import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisSession, WSEvent } from "../api/types";
import { useDiagnosisStore } from "../store/diagnosisStore";
import DiagnosisModifiedPage from "./DiagnosisModified";
import * as diagnosisModifiedModel from "./diagnosisModifiedModel";
import type {
  DiagnosisModifiedDemoScenario,
  DiagnosisModifiedLiveView,
  DiagnosisModifiedTimelineItem,
} from "./diagnosisModifiedModel";

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
  title: "演示诊断结论",
  subtitle: "演示摘要",
  certaintyLabel: "较大概率",
  certaintyTone: "accent",
  confidenceLabel: "80%",
  affectedServices: ["auth-svc"],
  impactSummary: "演示影响摘要",
};

const basePlan: DiagnosisModifiedDemoScenario["plan"] = {
  title: "演示修复方案",
  description: "演示修复方案说明",
  priorityLabel: "P1",
  confidenceLabel: "80%",
  steps: [],
};

function buildPlanWithSteps(stepTitle = "restart deployment"): DiagnosisModifiedDemoScenario["plan"] {
  return {
    ...basePlan,
    steps: [
      {
        id: "plan-step-1",
        title: stepTitle,
        detail: "调用 remediation.execute_plan",
        paramsSummary: "namespace=svc | name=api",
        status: "pending",
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
    liveThinking: null,
    streamingText: "",
    streamingNode: null,
    isStreamingDiagnosis: false,
    activeStreamingTools: [],
    streamingAbortController: null,
    bootstrapSession: vi.fn().mockResolvedValue(undefined),
    sendMessage: vi.fn().mockResolvedValue(undefined),
    revisePlan: vi.fn().mockResolvedValue(undefined),
    approvePlan: vi.fn().mockResolvedValue(undefined),
    applyEvent: current.applyEvent,
    setConnectionState: current.setConnectionState,
    ...overrides,
  });
}

function renderDemoPage() {
  return render(
    <MemoryRouter initialEntries={["/diagnosis"]}>
      <Routes>
        <Route path="/diagnosis" element={<DiagnosisModifiedPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderLivePage(path = "/diagnosis/sess-live") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/diagnosis/:sessionId" element={<DiagnosisModifiedPage />} />
        <Route path="/history/:sessionId" element={<DiagnosisModifiedPage />} />
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

function buildEmptyLiveView(): DiagnosisModifiedLiveView {
  return {
    timeline: [],
    candidates: [],
    summary: undefined,
    plan: undefined,
  };
}

describe("DiagnosisModifiedPage sequential playback", () => {
  const mockedBuildDemoScenario = vi.mocked(diagnosisModifiedModel.buildDiagnosisModifiedDemoScenario);
  const mockedBuildLiveView = vi.mocked(diagnosisModifiedModel.buildDiagnosisModifiedLiveView);

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    resetDiagnosisStore();
    mockedBuildLiveView.mockReturnValue(buildEmptyLiveView());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders the page heading as 诊断（修改） instead of a literal unicode escape string", () => {
    renderDemoPage();

    expect(screen.getByRole("heading", { name: "诊断（修改）" })).toBeInTheDocument();
    expect(screen.queryByText("\\u8bca\\u65ad\\uff08\\u4fee\\u6539\\uff09")).not.toBeInTheDocument();
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

    const input = screen.getByPlaceholderText(/请输入诊断问题/i);
    fireEvent.change(input, { target: { value: "demo request" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(120);

    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(2);
    expect(screen.queryByText("second-stream")).not.toBeInTheDocument();

    await flushPendingTimers();

    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(3);
  });

  it("collapses finished thinking into a unified 已思考 x 秒 label", async () => {
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
            title: "Agent 正在理解请求",
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

    const input = screen.getByPlaceholderText(/请输入诊断问题/i);
    fireEvent.change(input, { target: { value: "demo thinking" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await flushPendingTimers();

    expect(screen.getByText(/已思考 \d+ 秒/)).toBeInTheDocument();
    expect(screen.queryByText("Agent 正在理解请求")).not.toBeInTheDocument();
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
            summaryLines: ["拉取 metrics 中..."],
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

    const input = screen.getByPlaceholderText(/请输入诊断问题/i);
    fireEvent.change(input, { target: { value: "check tool sequence" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(200);

    expect(screen.getByText("正在查询")).toBeInTheDocument();
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
            summaryLines: ["拉取 metrics 中..."],
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

    const input = screen.getByPlaceholderText(/请输入诊断问题/i);
    fireEvent.change(input, { target: { value: "verify minimum dwell" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await advance(120);

    expect(screen.getByText("正在查询")).toBeInTheDocument();

    await advance(3200);

    expect(screen.getByText("正在查询")).toBeInTheDocument();
    expect(screen.queryByText("查询完成")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".diagnosis-modified-message-row")).toHaveLength(1);

    await advance(1500);

    expect(screen.getByText("查询完成")).toBeInTheDocument();

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
            title: "Agent 推理中",
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
            summaryLines: ["拉取 metrics 中..."],
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

    const input = screen.getByPlaceholderText(/请输入诊断问题/i);
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

    renderLivePage("/diagnosis/sess-live-timeout");

    timelineSource = [
      {
        id: "live-tool-1",
        kind: "tool",
        toolName: "query_slow_backend",
        params: { service: "auth-svc" },
        timestamp: "2026-04-08T11:00:01.000Z",
        status: "loading",
        summaryLines: ["等待 tool_result 返回..."],
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

    expect(screen.getByText("正在查询")).toBeInTheDocument();
    expect(screen.queryByText("post-timeout")).not.toBeInTheDocument();

    await advance(15000);
    await advance(1200);

    expect(screen.getByText("查询超时")).toBeInTheDocument();
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

    const { container } = renderLivePage("/history/sess-live-history");

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

  it("renders report card labels as proper Chinese text instead of literal unicode escapes", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [
        {
          id: "report-msg-1",
          kind: "message",
          role: "assistant",
          content: "report-ready",
          timestamp: "2026-04-08T11:30:00.000Z",
        },
      ],
      candidates: [
        {
          id: "candidate-1",
          rank: 1,
          title: "GPU 争用",
          summary: "GPU 争用摘要",
          confidence: 0.82,
          confidenceLabel: "0.82",
          statusLabel: "已确认",
          statusTone: "accent",
          evidenceSummary: "GPU queue 已经饱和",
          distinguishingVerification: "检查 worker-03 的进程列表",
          layer: "hardware",
          entities: ["worker-03", "gpu-0"],
          isPrimary: true,
          evidenceFor: ["gpu_util 持续接近 100%"],
          evidenceAgainst: [],
        },
      ],
      hypotheses: [
        {
          id: "hypothesis-1",
          description: "GPU queue 饱和",
          statusLabel: "已确认",
          statusTone: "accent",
          evidenceForCount: 2,
          evidenceAgainstCount: 0,
          confidence: 0.82,
        },
      ],
      propagationChain: [
        {
          id: "propagation-1",
          entityId: "worker-03",
          entityType: "node",
          metric: "gpu_util",
          valueBefore: "35%",
          valueAfter: "99%",
          description: "GPU 利用率明显升高",
        },
      ],
      summary: {
        title: "诊断报告",
        subtitle: "当前诊断",
        certaintyLabel: "已确认",
        certaintyTone: "accent",
        confidenceLabel: "82%",
        confidenceRawLabel: "0.82",
        priorityLabel: "P1",
        impactSummary: "auth-svc 时延升高",
        affectedServices: ["auth-svc"],
        rootCause: "GPU 争用",
        rootCauseLayer: "hardware",
        rootCauseLayerLabel: "硬件",
        rootCauseEntities: ["worker-03", "gpu-0"],
      },
      plan: undefined,
    });

    resetDiagnosisStore({
      session: createLiveSession("sess-live-report"),
      activeSessionId: "sess-live-report",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-report");

    expect(screen.getByText("当前结论")).toBeInTheDocument();
    expect(screen.getByText("候选根因（1）")).toBeInTheDocument();
    expect(screen.getByText("假设与证据")).toBeInTheDocument();
    expect(screen.getByText("传播链路（折叠）")).toBeInTheDocument();
    expect(screen.queryByText("\\u5f53\\u524d\\u7ed3\\u8bba")).not.toBeInTheDocument();
  });

  it("uses real store actions for live approval and revise flows", () => {
    const approveSpy = vi.fn().mockResolvedValue(undefined);
    const reviseSpy = vi.fn().mockResolvedValue(undefined);

    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [],
      summary: baseSummary,
      plan: buildPlanWithSteps(),
    });

    resetDiagnosisStore({
      session: { ...createLiveSession("sess-live-approval"), status: "approval_required" },
      activeSessionId: "sess-live-approval",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      canApprove: true,
      hasPlan: true,
      latestPlanVersion: 3,
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
      approvePlan: approveSpy,
      revisePlan: reviseSpy,
    });

    renderLivePage("/diagnosis/sess-live-approval");

    fireEvent.click(screen.getByRole("button", { name: "审批通过" }));
    expect(approveSpy).toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "修改方案" }));
    fireEvent.change(screen.getByPlaceholderText("例如：避免一次性全量发布，先从 canary 验证开始。"), {
      target: { value: "先只执行 canary 批次" },
    });
    fireEvent.click(screen.getByRole("button", { name: "更新方案" }));

    expect(reviseSpy).toHaveBeenCalledWith("先只执行 canary 批次");
  });

  it("keeps demo approval local and labels the card as a non-real execution path", async () => {
    const approveSpy = vi.fn().mockResolvedValue(undefined);

    mockedBuildDemoScenario.mockReturnValue({
      initialTimeline: [
        {
          id: "demo-user-approval",
          kind: "message",
          role: "user",
          content: "demo approval",
          timestamp: "2026-04-08T12:00:00.000Z",
        },
      ],
      events: [{ delayMs: 0, type: "complete" }],
      candidates: [],
      summary: baseSummary,
      plan: buildPlanWithSteps(),
    });

    resetDiagnosisStore({
      approvePlan: approveSpy,
    });

    renderDemoPage();

    const composer = document.querySelector(".diagnosis-modified-composer__input") as HTMLTextAreaElement | null;
    expect(composer).not.toBeNull();
    fireEvent.change(composer!, { target: { value: "demo approval" } });
    fireEvent.keyDown(composer!, { key: "Enter", code: "Enter" });

    await flushPendingTimers();

    expect(screen.getByText("当前为演示模式，审批按钮只会更新前端演示状态，不会触发真实修复执行。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "审批通过" }));

    expect(approveSpy).not.toHaveBeenCalled();
    expect(screen.getByText("方案已批准，Agent 将按计划执行后续动作。")).toBeInTheDocument();
  });

  it("disables live approval buttons when the session is not approval_required", () => {
    mockedBuildLiveView.mockReturnValue({
      timeline: [],
      candidates: [],
      summary: baseSummary,
      plan: buildPlanWithSteps(),
    });

    resetDiagnosisStore({
      session: { ...createLiveSession("sess-live-blocked"), status: "diagnosing" },
      activeSessionId: "sess-live-blocked",
      bootstrapStatus: "ready",
      traceStatus: "ready",
      canApprove: false,
      hasPlan: true,
      approvalBlockReason: "当前状态为 diagnosing，暂不可审批。",
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
    });

    renderLivePage("/diagnosis/sess-live-blocked");

    expect(screen.getByRole("button", { name: "审批通过" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
    expect(screen.getByText("当前状态为 diagnosing，暂不可审批。")).toBeInTheDocument();
  });

  it("renders only one next-action entry when duplicate live events are replayed", async () => {
    const actualBuildLiveView = await vi.importActual<typeof import("./diagnosisModifiedModel")>(
      "./diagnosisModifiedModel",
    );
    mockedBuildLiveView.mockImplementation((session, messages) =>
      actualBuildLiveView.buildDiagnosisModifiedLiveView(session, messages),
    );

    resetDiagnosisStore({
      session: {
        ...createLiveSession("sess-live-dedupe"),
        trace: { steps: [] },
      },
      activeSessionId: "sess-live-dedupe",
      bootstrapStatus: "ready",
      traceStatus: "empty",
      messages: [],
      bootstrapSession: vi.fn().mockResolvedValue(undefined),
      applyEvent: useDiagnosisStore.getState().applyEvent,
    });

    renderLivePage("/diagnosis/sess-live-dedupe");

    const thinkingEvent: WSEvent = {
      schema_version: "1",
      type: "thinking_step",
      session_id: "sess-live-dedupe",
      timestamp: "2026-04-08T11:20:00.000Z",
      data: {
        event_id: "evt-live-dedupe-1",
        step: 1,
        thought: "Verify auth service metrics before concluding",
        action_type: "tool_call",
        tool_name: "query_metrics",
        tool_params: { service: "auth-svc" },
      },
    };

    act(() => {
      useDiagnosisStore.getState().applyEvent(thinkingEvent);
      useDiagnosisStore.getState().applyEvent(thinkingEvent);
    });

    await advance(5000);

    expect(screen.queryByText("下一步：调用 query_metrics，目标 service=auth-svc，验证当前假设。")).not.toBeInTheDocument();
    expect(screen.getAllByText("Verify auth service metrics before concluding")).toHaveLength(1);
  });
});
