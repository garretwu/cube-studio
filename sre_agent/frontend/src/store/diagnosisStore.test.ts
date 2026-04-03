import { beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";

import type { WSEvent } from "../api/types";
import { diagnosisSession, initialChatMessages } from "../mocks/data";
import { server } from "../test/server";
import { useDiagnosisStore } from "./diagnosisStore";

describe("useDiagnosisStore", () => {
  beforeEach(() => {
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

  it("hydrates the active session and resets the conversation when the session changes", async () => {
    await useDiagnosisStore.getState().bootstrapSession();

    await useDiagnosisStore.getState().sendMessage("追问上一轮诊断");

    expect(useDiagnosisStore.getState().messages).toHaveLength(initialChatMessages.length + 2);
    expect(useDiagnosisStore.getState().activeSessionId).toBe(diagnosisSession.session_id);

    server.use(
      http.get("/api/sessions/:sessionId", async ({ params }) =>
        HttpResponse.json({
          ...diagnosisSession,
          session_id: String(params.sessionId ?? "sess-latency-002"),
        }),
      ),
    );

    await useDiagnosisStore.getState().bootstrapSession("sess-latency-002");

    expect(useDiagnosisStore.getState().session?.session_id).toBe("sess-latency-002");
    expect(useDiagnosisStore.getState().activeSessionId).toBe("sess-latency-002");
    expect(useDiagnosisStore.getState().messages).toEqual(initialChatMessages);
  });

  it("switches to empty state when backend has no sessions", async () => {
    server.use(http.get("/api/sessions", async () => HttpResponse.json([])));

    await useDiagnosisStore.getState().bootstrapSession();

    const state = useDiagnosisStore.getState();
    expect(state.bootstrapStatus).toBe("empty");
    expect(state.activeSessionId).toBeUndefined();
    expect(state.messages).toEqual([]);
  });

  it("applies websocket events into trace data without appending synthetic chat messages", async () => {
    await useDiagnosisStore.getState().bootstrapSession();

    const baselineMessageCount = useDiagnosisStore.getState().messages.length;
    const baselineTraceCount = useDiagnosisStore.getState().session?.trace?.steps.length ?? 0;

    const thinkingEvent: WSEvent = {
      schema_version: "1",
      type: "thinking_step",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:06:10Z",
      data: {
        step: 3,
        thought: "正在核对热点 GPU 与排队深度的关系",
        action_type: "tool_call",
        tool_name: "metrics.query",
      },
    };

    const resultEvent: WSEvent = {
      schema_version: "1",
      type: "tool_result",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:06:15Z",
      data: {
        tool_name: "metrics.query",
        result: {
          value: 0.94,
          trend: "up",
        },
      },
    };

    useDiagnosisStore.getState().applyEvent(thinkingEvent);
    useDiagnosisStore.getState().applyEvent(resultEvent);

    const state = useDiagnosisStore.getState();

    expect(state.messages).toHaveLength(baselineMessageCount);
    expect(state.session?.trace?.steps).toHaveLength(baselineTraceCount + 2);
    expect(state.traceStatus).toBe("ready");
    expect(state.session?.trace?.steps?.at(-2)).toMatchObject({
      thought: "正在核对热点 GPU 与排队深度的关系",
      tool_name: "metrics.query",
    });
    expect(state.session?.trace?.steps?.at(-1)).toMatchObject({
      tool: "metrics.query",
    });
  });

  it("uses default instruction when revising plan without input", async () => {
    let capturedInstruction = "";
    server.use(
      http.post("/api/remediate/:sessionId/plan/revise", async ({ request, params }) => {
        const body = (await request.json()) as { instruction?: string };
        capturedInstruction = String(body.instruction ?? "");
        return HttpResponse.json({
          session_id: String(params.sessionId ?? "sess-latency-001"),
          plan_version: 2,
          plan: {
            plan_id: "plan-v2",
            root_cause: "x",
            description: "y",
            steps: [],
            estimated_impact: "low",
            confidence: 0.7,
            priority: "P2",
          },
          session: {
            ...diagnosisSession,
            session_id: String(params.sessionId ?? "sess-latency-001"),
            status: "approval_required",
          },
        });
      }),
    );
    await useDiagnosisStore.getState().bootstrapSession("sess-latency-001");
    await useDiagnosisStore.getState().revisePlan("");

    expect(capturedInstruction).toBe("请优化当前修复方案，补充更稳妥步骤与验证");
  });

  it("appends escalation chat message when approval ends in execution_failed via polled events", async () => {
    const sessionId = diagnosisSession.session_id;
    let eventsRequestCount = 0;
    server.use(
      http.post("/api/remediate/:sessionId/approve", async () =>
        HttpResponse.json({
          plan_id: "plan-v1",
          success: false,
          steps_completed: 0,
          steps_total: 2,
          duration_seconds: 3,
          error: "execution failed",
        }),
      ),
      http.get("/api/sessions/:sessionId", async ({ params }) =>
        HttpResponse.json({
          ...diagnosisSession,
          session_id: String(params.sessionId ?? sessionId),
          status: "failed",
        }),
      ),
      http.get("/api/sessions/:sessionId/events", async ({ params }) =>
        HttpResponse.json(
          eventsRequestCount++ === 0
            ? []
            : [
                {
                  schema_version: "1",
                  type: "remediation_progress",
                  session_id: String(params.sessionId ?? sessionId),
                  timestamp: "2026-04-02T10:00:00Z",
                  data: {
                    stage: "execution_failed",
                    message: "修复执行失败",
                    plan_version: 1,
                  },
                },
              ],
        ),
      ),
    );

    await useDiagnosisStore.getState().bootstrapSession(sessionId);
    const baselineCount = useDiagnosisStore
      .getState()
      .messages.filter((message) => message.content === "需要工程师介入").length;
    await useDiagnosisStore.getState().approvePlan(true);

    const state = useDiagnosisStore.getState();
    expect(state.session?.status).toBe("failed");
    const escalationMessages = state.messages.filter((message) => message.content === "需要工程师介入");
    expect(escalationMessages.length).toBeGreaterThan(baselineCount);
  });
});
