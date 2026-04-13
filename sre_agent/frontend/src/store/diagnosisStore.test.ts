import { beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";

import type { WSEvent } from "../api/types";
import { diagnosisSession, initialChatMessages } from "../mocks/data";
import { server } from "../test/server";
import { useDiagnosisStore } from "./diagnosisStore";

describe("useDiagnosisStore", () => {
  beforeEach(() => {
    window.localStorage.removeItem("sre_session_id");
    useDiagnosisStore.setState({
      session: undefined,
      activeSessionId: undefined,
      messages: [],
      events: [],
      localAuditRecords: [],
      isLoadingSession: false,
      bootstrapStatus: "idle",
      traceStatus: "unknown",
      isSendingMessage: false,
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
      liveThinking: null,
      streamingText: "",
      streamingNode: null,
      isStreamingDiagnosis: false,
      activeStreamingTools: [],
      streamingAbortController: null,
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

  it("aggregates live node events into a single streaming thinking block and keeps backend-only semantics", async () => {
    await useDiagnosisStore.getState().bootstrapSession();

    const nodeStarted: WSEvent = {
      schema_version: "1",
      type: "node_started",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:07:00Z",
      data: {
        node: "reason",
        run_id: "run-reason-1",
        thought_key: "run-reason-1:reason",
        started_at: "2026-03-18T12:07:00Z",
      },
    };
    const tokenOne: WSEvent = {
      schema_version: "1",
      type: "token_delta",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:07:01Z",
      data: {
        node: "reason",
        run_id: "run-reason-1",
        thought_key: "run-reason-1:reason",
        content: "Investigating queue depth. ",
      },
    };
    const toolStarted: WSEvent = {
      schema_version: "1",
      type: "tool_started",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:07:01Z",
      data: {
        tool: "query_metrics",
        params: { service: "auth-svc" },
        node: "reason",
        run_id: "run-reason-1",
        thought_key: "run-reason-1:reason",
      },
    };
    const tokenTwo: WSEvent = {
      schema_version: "1",
      type: "token_delta",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:07:02Z",
      data: {
        node: "reason",
        run_id: "run-reason-1",
        thought_key: "run-reason-1:reason",
        content: "Comparing p95 against baseline.",
      },
    };
    const nodeCompleted: WSEvent = {
      schema_version: "1",
      type: "node_completed",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:07:03Z",
      data: {
        node: "reason",
        run_id: "run-reason-1",
        thought_key: "run-reason-1:reason",
        thought_duration_sec: 3,
        new_trace_items: [
          {
            event_type: "tool_call",
            step: 3,
            timestamp: "2026-03-18T12:07:03Z",
            thought: "Investigating queue depth. Comparing p95 against baseline.",
            action_type: "tool_call",
            thought_key: "run-reason-1:reason",
            tool_name: "query_metrics",
            tool_params: { service: "auth-svc" },
            thought_duration_sec: 3,
          },
        ],
      },
    };

    useDiagnosisStore.getState().applyEvent(nodeStarted);
    useDiagnosisStore.getState().applyEvent(tokenOne);
    useDiagnosisStore.getState().applyEvent(toolStarted);
    useDiagnosisStore.getState().applyEvent(tokenTwo);

    let state = useDiagnosisStore.getState();
    expect(state.liveThinking).toMatchObject({
      thought_key: "run-reason-1:reason",
      node: "reason",
      content: "Investigating queue depth. Comparing p95 against baseline.",
    });
    expect(state.activeStreamingTools).toHaveLength(1);
    expect(state.activeStreamingTools[0]).toMatchObject({
      tool: "query_metrics",
      thought_key: "run-reason-1:reason",
    });

    useDiagnosisStore.getState().applyEvent(nodeCompleted);

    state = useDiagnosisStore.getState();
    expect(state.liveThinking).toBeNull();
    expect(state.activeStreamingTools).toEqual([]);
    expect(state.session?.trace?.steps?.at(-1)).toMatchObject({
      thought: "Investigating queue depth. Comparing p95 against baseline.",
      thought_key: "run-reason-1:reason",
      thought_duration_sec: 3,
    });
    expect(state.session?.trace?.steps?.at(-1)).not.toHaveProperty("next_action");
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

  it("restores session-scoped local audit records during bootstrap", async () => {
    window.localStorage.setItem(
      "sre_diagnosis_local_audit_v1",
      JSON.stringify({
        [diagnosisSession.session_id]: [
          {
            id: "audit-local-1",
            sessionId: diagnosisSession.session_id,
            eventKind: "approval_result",
            source: "local_audit",
            dedupeKey: "approval-result-rejected-v3",
            timestamp: "2026-04-08T11:30:00.000Z",
            summary: "[系统] 已审批，拒绝执行（原因：需要人工复核）",
            details: ["拒绝原因：需要人工复核"],
            statusTone: "danger",
          },
        ],
      }),
    );

    await useDiagnosisStore.getState().bootstrapSession(diagnosisSession.session_id);

    expect(useDiagnosisStore.getState().localAuditRecords).toHaveLength(1);
    expect(useDiagnosisStore.getState().localAuditRecords[0]?.summary).toContain("拒绝执行");
  });
});
