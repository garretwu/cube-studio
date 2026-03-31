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
      messages: [...initialChatMessages],
      isLoadingSession: false,
      isSendingMessage: false,
      connectionState: "closed",
      error: undefined,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders diagnosis chat workspace with script trigger", async () => {
    renderDiagnosisPage("/diagnosis/sess-latency-001");

    await waitFor(() => {
      expect(screen.getByText(initialChatMessages[0].content)).toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { name: "诊断对话" })).toBeInTheDocument();
    expect(screen.getByText(/会话 sess-latency-001/)).toBeInTheDocument();
    expect(screen.getByText(/当前诊断推理链路已经合并进会话/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/继续追问当前诊断/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mock步骤" })).toBeInTheDocument();
  });

  it("runs step1 then unlocks step2 in script panel", async () => {
    const user = userEvent.setup();

    renderDiagnosisPage("/diagnosis/sess-latency-001");

    await waitFor(() => {
      expect(screen.getByText(initialChatMessages[0].content)).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Mock步骤" }));

    expect(screen.getByText("Step 1 · 确认影响范围")).toBeInTheDocument();
    expect(screen.getByText("Step 2 · 规划观测动作")).toBeInTheDocument();

    const step2Locked = screen.getByRole("button", { name: "等待解锁" });
    expect(step2Locked).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "执行此步" }));

    await waitFor(() => {
      expect(screen.getByText(/\[Step 1 完成\]/)).toBeInTheDocument();
    });

    const step2RunButton = screen.getByRole("button", { name: "执行此步" });
    expect(step2RunButton).toBeEnabled();

    await user.click(step2RunButton);

    await waitFor(() => {
      expect(screen.getByText(/\[Step 2 完成\]/)).toBeInTheDocument();
    });
  });

  it("shows realtime thinking_step content in analysis bubble", async () => {
    vi.stubEnv("VITE_WS_ENABLED", "true");
    websocketState = "open";

    renderDiagnosisPage("/diagnosis/sess-latency-001");

    await waitFor(() => {
      expect(emitWebsocketEvent).toBeTypeOf("function");
    });

    act(() => {
      emitWebsocketEvent?.({
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
      expect(screen.getByText("Comparing GPU utilization with queue latency")).toBeInTheDocument();
    });
  });
});
