import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { WSEvent } from "../api/types";
import { initialChatMessages } from "../mocks/data";
import { useDiagnosisStore } from "../store/diagnosisStore";
import DiagnosisPage from "./Diagnosis";

let websocketState: "connecting" | "open" | "closed" | "error" = "closed";
let emitWebsocketEvent: ((event: WSEvent) => void) | undefined;

vi.mock("../hooks/useWebSocket", () => ({
  useWebSocket: (_url: string, onEvent: (event: WSEvent) => void) => {
    emitWebsocketEvent = onEvent;
    return { state: websocketState };
  },
}));

function renderDiagnosisPage(initialEntry = "/diagnosis") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/diagnosis" element={<DiagnosisPage />} />
        <Route path="/diagnosis/:sessionId" element={<DiagnosisPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DiagnosisPage", () => {
  beforeEach(() => {
    websocketState = "closed";
    emitWebsocketEvent = undefined;
    vi.unstubAllEnvs();
    vi.useRealTimers();

    useDiagnosisStore.setState({
      session: undefined,
      activeSessionId: undefined,
      messages: [],
      events: [],
      isLoadingSession: false,
      bootstrapStatus: "idle",
      traceStatus: "unknown",
      isSendingMessage: false,
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
      connectionState: "closed",
      error: undefined,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders diagnosis chat workspace with script trigger", async () => {
    renderDiagnosisPage("/diagnosis/sess-latency-001");

    await waitFor(() => {
      expect(screen.getByText(initialChatMessages[0].content)).toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { name: "诊断对话" })).toBeInTheDocument();
    expect(screen.getByText(/会话 sess-latency-001/)).toBeInTheDocument();
    expect(screen.getByText(/保留诊断交互与 WebSocket 事件消费/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/继续追问当前诊断/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "模拟演示" })).toBeInTheDocument();
  });

  it("runs step1 then unlocks step2 in script panel", async () => {
    const user = userEvent.setup();

    renderDiagnosisPage("/diagnosis/sess-latency-001");

    await waitFor(() => {
      expect(screen.getByText(initialChatMessages[0].content)).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "模拟演示" }));
    await user.click(await screen.findByText(/1、确认影响范围/));

    await waitFor(
      () => {
        expect(screen.getByText(/受影响节点：node-gpu-01/)).toBeInTheDocument();
      },
      { timeout: 4000 },
    );

    await user.click(screen.getByRole("button", { name: "模拟演示" }));
    await user.click(await screen.findByText(/2、规划观测动作/));

    await waitFor(
      () => {
        expect(screen.getByText(/下一步工具选择/)).toBeInTheDocument();
      },
      { timeout: 6000 },
    );
  });

  it("applies realtime thinking_step events into diagnosis trace state", async () => {
    vi.stubEnv("VITE_WS_ENABLED", "true");
    websocketState = "open";

    renderDiagnosisPage("/diagnosis/sess-latency-001");

    await waitFor(() => {
      expect(useDiagnosisStore.getState().session?.session_id).toBe("sess-latency-001");
    });

    act(() => {
      useDiagnosisStore.getState().applyEvent({
        schema_version: "1",
        type: "thinking_step",
        session_id: "sess-latency-001",
        timestamp: "2026-03-18T12:06:10Z",
        data: {
          step: 1,
          thought: "Comparing GPU utilization with queue latency",
          action_type: "tool_call",
          tool_name: "metrics.query",
        },
      });
    });

    await waitFor(() => {
      expect(useDiagnosisStore.getState().session?.trace?.steps ?? []).toContainEqual(
        expect.objectContaining({
          thought: "Comparing GPU utilization with queue latency",
          tool_name: "metrics.query",
        }),
      );
    });
  });

  it("always renders remediation approval card and blocks approval when status is not approval_required", async () => {
    renderDiagnosisPage("/diagnosis/sess-latency-001");

    await waitFor(() => {
      expect(screen.getByText("修复审批")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "审批通过" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /拒\s*绝/ })).toBeDisabled();
    expect(screen.getByText(/当前状态：/)).toBeInTheDocument();
  });

  it("allows revise submission without typed instruction", async () => {
    const user = userEvent.setup();
    const reviseSpy = vi.spyOn(useDiagnosisStore.getState(), "revisePlan").mockResolvedValue(undefined);

    renderDiagnosisPage("/diagnosis/sess-latency-001");

    const reviseButton = await screen.findByRole("button", { name: "修改方案" });
    expect(reviseButton).toBeEnabled();
    await user.click(reviseButton);

    await waitFor(() => {
      expect(reviseSpy).toHaveBeenCalledWith("");
    });
  });

  it("keeps approval card visible even when plan details are missing", async () => {
    renderDiagnosisPage("/diagnosis/sess-latency-001");

    expect(await screen.findByText("修复审批")).toBeInTheDocument();
    expect(screen.queryByText(/根因：/)).not.toBeInTheDocument();
    expect(screen.queryByText(/步骤 \d+：/)).not.toBeInTheDocument();
  });
});

