import { beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";

import type { WSEvent } from "../api/types";
import { diagnosisSession, initialChatMessages } from "../mocks/data";
import { server } from "../test/server";
import { useDiagnosisStore } from "./diagnosisStore";

describe("useDiagnosisStore", () => {
  beforeEach(() => {
    server.use(
      http.post("/api/diagnose/start", async () => HttpResponse.json(diagnosisSession)),
    );
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
      sessionResolveState: "resolved",
      sessionResolveSource: "none",
      approvalOverlayOpen: false,
      currentPlanVersion: null,
      latestPlanVersion: null,
      approvedPlanVersion: null,
      canApprove: false,
      approvalBlockReason: undefined,
      hasPlan: false,
      planMissingReason: undefined,
      effectiveReviseInstruction: undefined,
      completedThinkingRounds: [],
      liveThinking: null,
      liveFinalAnswer: null,
      streamingText: "",
      streamingNode: null,
      isStreamingDiagnosis: false,
      streamingPhase: "idle",
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

  it("redirects stale session id to latest available session", async () => {
    server.use(
      http.get("/api/sessions/sess-stale", async () => HttpResponse.json({ message: "session not found" }, { status: 404 })),
      http.get("/api/sessions", async () =>
        HttpResponse.json([
          {
            session_id: "sess-latest",
            status: "diagnosing",
            alert_name: "Latest session",
            severity: "warning",
            fingerprint: "sess-latest",
            outcome: null,
            duration_seconds: 2,
            updated_at: "2026-04-20T00:00:02Z",
          },
        ]),
      ),
      http.get("/api/sessions/sess-latest", async () =>
        HttpResponse.json({
          ...diagnosisSession,
          session_id: "sess-latest",
          status: "diagnosing",
          trace: { steps: [] },
        }),
      ),
      http.get("/api/sessions/:sessionId/events", async () => HttpResponse.json([])),
      http.get("/api/chat/history", async () => HttpResponse.json([])),
    );

    await useDiagnosisStore.getState().bootstrapSession("sess-stale");

    const state = useDiagnosisStore.getState();
    expect(state.bootstrapStatus).toBe("ready");
    expect(state.activeSessionId).toBe("sess-latest");
    expect(state.sessionResolveState).toBe("stale_redirected");
    expect(state.sessionResolveSource).toBe("latest");
    expect(state.error ?? "").toContain("会话已失效");
  });

  it("surfaces approve API failures as remediation approval errors without clearing the session", async () => {
    const approvalSession = {
      ...diagnosisSession,
      session_id: "sess-approval-error",
      status: "approval_required" as const,
    };
    server.use(
      http.post("/api/remediate/:sessionId/approve", async () =>
        HttpResponse.json({ message: "backend boom" }, { status: 500 })),
    );
    useDiagnosisStore.setState((state) => ({
      ...state,
      session: approvalSession,
      activeSessionId: approvalSession.session_id,
      bootstrapStatus: "ready",
      traceStatus: "ready",
      latestPlanVersion: 3,
      canApprove: true,
      approvalOverlayOpen: true,
    }));

    await expect(useDiagnosisStore.getState().approvePlan(true)).rejects.toThrow("Request failed with status 500.");

    const state = useDiagnosisStore.getState();
    expect(state.session?.session_id).toBe("sess-approval-error");
    expect(state.error).toBe("修复执行审批失败：Request failed with status 500.");
    expect(state.approvalOverlayOpen).toBe(true);
    expect(state.isApprovingPlan).toBe(false);
  });

  it("keeps diagnosis bootstrap ready when chat history request fails", async () => {
    server.use(
      http.get("/api/chat/history", async () => HttpResponse.json({ message: "upstream timeout" }, { status: 504 })),
    );

    await useDiagnosisStore.getState().bootstrapSession();

    const state = useDiagnosisStore.getState();
    expect(state.bootstrapStatus).toBe("ready");
    expect(state.session?.session_id).toBe(diagnosisSession.session_id);
    expect(state.messages).toEqual([]);
    expect(state.error).toContain("部分补充信息加载较慢");
    expect(state.error).toContain("对话历史未完全加载");
  });

  it("keeps diagnosis bootstrap ready when session events request fails", async () => {
    server.use(
      http.get("/api/sessions/:sessionId/events", async () => HttpResponse.json({ message: "gateway timeout" }, { status: 504 })),
    );

    await useDiagnosisStore.getState().bootstrapSession();

    const state = useDiagnosisStore.getState();
    expect(state.bootstrapStatus).toBe("ready");
    expect(state.session?.session_id).toBe(diagnosisSession.session_id);
    expect(state.events).toEqual([]);
    expect(state.error).toContain("部分补充信息加载较慢");
    expect(state.error).toContain("会话事件未完全加载");
  });

  it("keeps diagnosis bootstrap ready when chat history and events both fail", async () => {
    server.use(
      http.get("/api/chat/history", async () => HttpResponse.json({ message: "upstream timeout" }, { status: 504 })),
      http.get("/api/sessions/:sessionId/events", async () => HttpResponse.json({ message: "gateway timeout" }, { status: 504 })),
    );

    await useDiagnosisStore.getState().bootstrapSession();

    const state = useDiagnosisStore.getState();
    expect(state.bootstrapStatus).toBe("ready");
    expect(state.session?.session_id).toBe(diagnosisSession.session_id);
    expect(state.messages).toEqual([]);
    expect(state.events).toEqual([]);
    expect(state.error).toContain("对话历史未完全加载");
    expect(state.error).toContain("会话事件未完全加载");
  });

  it("keeps diagnosis bootstrap in error state when the session request fails", async () => {
    server.use(
      http.get("/api/sessions", async () => HttpResponse.json([{ session_id: diagnosisSession.session_id }])),
      http.get("/api/sessions/:sessionId", async () => HttpResponse.json({ message: "backend timeout" }, { status: 504 })),
    );

    await useDiagnosisStore.getState().bootstrapSession();

    const state = useDiagnosisStore.getState();
    expect(state.bootstrapStatus).toBe("error");
    expect(state.session).toBeUndefined();
    expect(state.error).toContain("Request failed with status 504");
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

  it("routes final content token deltas into the live final answer buffer", async () => {
    await useDiagnosisStore.getState().bootstrapSession();

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "token_delta",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:08:00Z",
      data: {
        content: "诊断结论：Node contention。",
        stream_channel: "content",
        phase: "final",
      },
    });
    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "done",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:08:01Z",
      data: {},
    });

    const state = useDiagnosisStore.getState();
    expect(state.liveThinking).toBeNull();
    expect(state.liveFinalAnswer).toMatchObject({
      content: "诊断结论：Node contention。",
      status: "completed",
    });
  });

  it("marks live thinking as completed and sets streamingPhase=error on error event", () => {
    useDiagnosisStore.setState({
      session: diagnosisSession,
      activeSessionId: diagnosisSession.session_id,
      isStreamingDiagnosis: true,
      streamingPhase: "streaming_thought",
      liveThinking: {
        thought_key: "run-reason-error:reason",
        run_id: "run-reason-error",
        node: "reason",
        timestamp: "2026-03-18T12:08:10Z",
        content: "",
        status: "thinking",
        tool_name: null,
        active_tools: [],
      },
      activeStreamingTools: [],
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "error",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:08:11Z",
      data: {
        message: "reason step timed out",
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.isStreamingDiagnosis).toBe(false);
    expect(state.streamingPhase).toBe("error");
    expect(state.liveThinking?.status).toBe("completed");
    expect(state.liveThinking?.content).toContain("reason step timed out");
  });

  it("closes streaming and keeps liveThinking non-thinking when done event arrives", () => {
    useDiagnosisStore.setState({
      session: diagnosisSession,
      activeSessionId: diagnosisSession.session_id,
      isStreamingDiagnosis: true,
      streamingPhase: "streaming_thought",
      liveThinking: {
        thought_key: "run-reason-done:reason",
        run_id: "run-reason-done",
        node: "reason",
        timestamp: "2026-03-18T12:08:12Z",
        content: "Investigating impact scope",
        status: "thinking",
        tool_name: null,
        active_tools: [],
      },
      activeStreamingTools: [],
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "done",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:08:13Z",
      data: {
        status: "diagnosed",
        summary: "诊断完成",
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.isStreamingDiagnosis).toBe(false);
    expect(state.streamingPhase).toBe("completed");
    expect(state.liveThinking?.status).toBe("completed");
    expect(state.liveThinking?.content).toBe("Investigating impact scope");
  });

  it("does not keep spinner on node_completed step_timeout terminal event", () => {
    useDiagnosisStore.setState({
      session: diagnosisSession,
      activeSessionId: diagnosisSession.session_id,
      isStreamingDiagnosis: true,
      streamingPhase: "streaming_thought",
      liveThinking: {
        thought_key: "run-reason-timeout:reason",
        run_id: "run-reason-timeout",
        node: "reason",
        timestamp: "2026-03-18T12:08:14Z",
        content: "",
        status: "thinking",
        tool_name: null,
        active_tools: [],
      },
      activeStreamingTools: [],
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "node_completed",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:08:15Z",
      data: {
        node: "reason",
        run_id: "run-reason-timeout",
        thought_key: "run-reason-timeout:reason",
        status: "step_timeout",
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.isStreamingDiagnosis).toBe(false);
    expect(state.streamingPhase).toBe("error");
    expect(state.liveThinking?.status).toBe("completed");
    expect(state.liveThinking?.content).toContain("超时");
  });

  it("captures plan_unavailable reason for sessions without remediation plan", () => {
    const sessionWithoutPlan = {
      ...diagnosisSession,
      session_id: "sess-no-plan",
      status: "diagnosed" as const,
      diagnosis_result: diagnosisSession.diagnosis_result
        ? {
            ...diagnosisSession.diagnosis_result,
            recommended_fix: undefined,
            root_cause: diagnosisSession.diagnosis_result.root_cause.map((rootCause) => ({
              ...rootCause,
              recommended_fix: undefined,
            })),
          }
        : undefined,
    };

    useDiagnosisStore.setState({
      session: sessionWithoutPlan,
      activeSessionId: "sess-no-plan",
      events: [],
      messages: [],
      hasPlan: false,
      planMissingReason: undefined,
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "remediation_progress",
      session_id: "sess-no-plan",
      timestamp: "2026-03-18T12:08:02Z",
      data: {
        stage: "plan_unavailable",
        message: "证据不足，暂不生成可执行修复计划。",
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.hasPlan).toBe(false);
    expect(state.planMissingReason).toBe("证据不足，暂不生成可执行修复计划。");
  });

  it("shows a thinking placeholder immediately on node_started before token deltas arrive", () => {
    useDiagnosisStore.setState({
      session: diagnosisSession,
      activeSessionId: diagnosisSession.session_id,
      events: [],
      messages: [],
      liveThinking: null,
      activeStreamingTools: [],
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "node_started",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:09:00Z",
      data: {
        node: "reason",
        run_id: "run-reason-placeholder",
        thought_key: "run-reason-placeholder:reason",
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.liveThinking).toMatchObject({
      thought_key: "run-reason-placeholder:reason",
      status: "thinking",
    });
    expect(state.liveThinking?.content).toBe("");
  });

  it("creates a new round_id for repeated thought_key rounds and resets content on the next round", () => {
    useDiagnosisStore.setState({
      session: diagnosisSession,
      activeSessionId: diagnosisSession.session_id,
      events: [],
      messages: [],
      liveThinking: null,
      activeStreamingTools: [],
      isStreamingDiagnosis: true,
      streamingPhase: "streaming_thought",
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "node_started",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:11:00Z",
      data: {
        node: "reason",
        run_id: "run-reason-rounds",
        thought_key: "run-reason-rounds:reason",
        started_at: "2026-03-18T12:11:00Z",
      },
    });
    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "token_delta",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:11:01Z",
      data: {
        node: "reason",
        run_id: "run-reason-rounds",
        thought_key: "run-reason-rounds:reason",
        content: "round-1 thinking",
      },
    });
    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "node_completed",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:11:02Z",
      data: {
        node: "reason",
        run_id: "run-reason-rounds",
        thought_key: "run-reason-rounds:reason",
        thought_duration_sec: 2,
      },
    });

    const afterRoundOne = useDiagnosisStore.getState();
    const roundOneId = afterRoundOne.liveThinking?.round_id;
    expect(afterRoundOne.liveThinking?.status).toBe("completed");
    expect(afterRoundOne.liveThinking?.content).toBe("round-1 thinking");

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "node_started",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:11:03Z",
      data: {
        node: "reason",
        run_id: "run-reason-rounds",
        thought_key: "run-reason-rounds:reason",
        started_at: "2026-03-18T12:11:03Z",
      },
    });

    const afterRoundTwoStarted = useDiagnosisStore.getState();
    expect(afterRoundTwoStarted.liveThinking?.status).toBe("thinking");
    expect(afterRoundTwoStarted.liveThinking?.content).toBe("");
    expect(afterRoundTwoStarted.liveThinking?.round_id).toBeDefined();
    expect(afterRoundTwoStarted.liveThinking?.round_id).not.toBe(roundOneId);
  });

  it("starts a fresh round on token_delta when the previous round is already completed", () => {
    useDiagnosisStore.setState({
      session: diagnosisSession,
      activeSessionId: diagnosisSession.session_id,
      isStreamingDiagnosis: true,
      streamingPhase: "streaming_thought",
      completedThinkingRounds: [],
      liveThinking: {
        round_id: "run-reason-token@2026-03-18T12:12:00Z",
        thought_key: "run-reason-token:reason",
        run_id: "run-reason-token",
        node: "reason",
        timestamp: "2026-03-18T12:12:00Z",
        content: "round-1 done",
        status: "completed",
        tool_name: null,
        active_tools: [],
      },
      activeStreamingTools: [],
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "token_delta",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:12:03Z",
      data: {
        node: "reason",
        run_id: "run-reason-token",
        thought_key: "run-reason-token:reason",
        content: "round-2 thinking",
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.liveThinking?.status).toBe("thinking");
    expect(state.liveThinking?.content).toBe("round-2 thinking");
    expect(state.liveThinking?.timestamp).toBe("2026-03-18T12:12:03Z");
    expect(state.liveThinking?.round_id).not.toBe("run-reason-token@2026-03-18T12:12:00Z");
    expect(state.completedThinkingRounds).toHaveLength(1);
    expect(state.completedThinkingRounds[0]).toMatchObject({
      thought_key: "run-reason-token:reason",
      content: "round-1 done",
      status: "completed",
    });
  });

  it("consumes completedThinkingRounds once node_completed snapshot includes the same thought_key", () => {
    useDiagnosisStore.setState({
      session: {
        ...diagnosisSession,
        trace: { steps: [] },
      },
      activeSessionId: diagnosisSession.session_id,
      isStreamingDiagnosis: true,
      streamingPhase: "streaming_thought",
      completedThinkingRounds: [
        {
          round_id: "run-reason-snapshot@2026-03-18T12:13:00Z",
          thought_key: "run-reason-snapshot:reason",
          run_id: "run-reason-snapshot",
          node: "reason",
          timestamp: "2026-03-18T12:13:00Z",
          content: "pending completed round",
          status: "completed",
          tool_name: null,
          active_tools: [],
        },
      ],
      liveThinking: {
        round_id: "run-reason-snapshot@2026-03-18T12:13:01Z",
        thought_key: "run-reason-snapshot:reason",
        run_id: "run-reason-snapshot",
        node: "reason",
        timestamp: "2026-03-18T12:13:01Z",
        content: "active round",
        status: "thinking",
        tool_name: null,
        active_tools: [],
      },
      activeStreamingTools: [],
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "node_completed",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-03-18T12:13:05Z",
      data: {
        node: "reason",
        run_id: "run-reason-snapshot",
        thought_key: "run-reason-snapshot:reason",
        new_trace_items: [
          {
            event_type: "thinking_step",
            step: 9,
            timestamp: "2026-03-18T12:13:04Z",
            thought: "snapshot absorbed this round",
            action_type: "conclude",
            thought_key: "run-reason-snapshot:reason",
            thought_duration_sec: 3,
          },
        ],
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.completedThinkingRounds).toEqual([]);
  });

  it("creates a bootstrap thinking block immediately when streaming diagnosis starts", async () => {
    server.use(
      http.post("/api/diagnose/stream", async () =>
        new HttpResponse("", {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );

    const pendingSessionId = useDiagnosisStore.getState().startStreamingDiagnosis(diagnosisSession.alert);
    const state = useDiagnosisStore.getState();

    expect(pendingSessionId).toMatch(/^pending-/);
    expect(state.activeSessionId).toBe(pendingSessionId);
    expect(state.session?.session_id).toBe(pendingSessionId);
    expect(state.streamingPhase).toBe("bootstrapping");
    expect(state.isStreamingDiagnosis).toBe(true);
    expect(state.liveThinking).toMatchObject({
      thought_key: `bootstrap:${pendingSessionId}`,
      node: "bootstrap",
      status: "thinking",
      content: "",
    });

    useDiagnosisStore.getState().cancelStreamingDiagnosis();
  });

  it("adopts the real session id when the first streaming event arrives for a pending session", () => {
    useDiagnosisStore.setState({
      session: {
        ...diagnosisSession,
        session_id: "pending-123",
        trace: { steps: [] },
      },
      activeSessionId: "pending-123",
      bootstrapStatus: "ready",
      traceStatus: "empty",
      isStreamingDiagnosis: true,
      streamingPhase: "bootstrapping",
      liveThinking: {
        thought_key: "bootstrap:pending-123",
        node: "bootstrap",
        run_id: null,
        timestamp: "2026-03-18T12:09:30Z",
        content: "",
        status: "thinking",
        tool_name: null,
        active_tools: [],
      },
    });

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "diagnosis_started",
      session_id: "sess-real-001",
      timestamp: "2026-03-18T12:09:31Z",
      data: {
        alert: {
          alert_name: diagnosisSession.alert.alert_name,
          severity: diagnosisSession.alert.severity,
          labels: diagnosisSession.alert.labels,
        },
        topology: null,
        variables: {},
        bootstrap_state: "thinking",
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.activeSessionId).toBe("sess-real-001");
    expect(state.session?.session_id).toBe("sess-real-001");
    expect(state.streamingPhase).toBe("waiting_first_content");
  });

  it("does not reset active streaming session during bootstrap for the same session id", async () => {
    useDiagnosisStore.setState({
      session: {
        ...diagnosisSession,
        session_id: "sess-live-001",
      },
      activeSessionId: "sess-live-001",
      bootstrapStatus: "ready",
      isStreamingDiagnosis: true,
      streamingPhase: "streaming_thought",
      liveThinking: {
        thought_key: "run-live:reason",
        node: "reason",
        run_id: "run-live",
        timestamp: "2026-03-18T12:10:01Z",
        content: "streaming...",
        status: "thinking",
        tool_name: null,
        active_tools: [],
      },
    });

    await useDiagnosisStore.getState().bootstrapSession("sess-live-001");

    const state = useDiagnosisStore.getState();
    expect(state.activeSessionId).toBe("sess-live-001");
    expect(state.isStreamingDiagnosis).toBe(true);
    expect(state.streamingPhase).toBe("streaming_thought");
    expect(state.liveThinking?.content).toBe("streaming...");
  });

  it("uses the latest diagnosis_started topology when historical events contain multiple starts", async () => {
    server.use(
      http.get("/api/sessions/:sessionId/events", async () =>
        HttpResponse.json([
          {
            schema_version: "1",
            type: "diagnosis_started",
            session_id: "sess-latency-001",
            timestamp: "2026-04-27T03:00:00.000Z",
            data: {
              alert: {
                alert_name: diagnosisSession.alert.alert_name,
                severity: diagnosisSession.alert.severity,
                labels: diagnosisSession.alert.labels,
              },
              topology: {
                roots: ["service:old-root"],
                affected_count: 1,
                affected_entities: [{ id: "pod:old", type: "k8s_pod", name: "old" }],
                summary: "old topology",
              },
              variables: {},
            },
          },
          {
            schema_version: "1",
            type: "thinking_step",
            session_id: "sess-latency-001",
            timestamp: "2026-04-27T03:01:00.000Z",
            data: { step: 1, thought: "collecting evidence" },
          },
          {
            schema_version: "1",
            type: "diagnosis_started",
            session_id: "sess-latency-001",
            timestamp: "2026-04-27T03:02:00.000Z",
            data: {
              alert: {
                alert_name: diagnosisSession.alert.alert_name,
                severity: diagnosisSession.alert.severity,
                labels: diagnosisSession.alert.labels,
              },
              topology: {
                roots: ["service:new-root"],
                affected_count: 2,
                affected_entities: [
                  { id: "pod:new-1", type: "k8s_pod", name: "new-1" },
                  { id: "node:new-node", type: "node", name: "new-node" },
                ],
                summary: "new topology",
              },
              variables: {},
            },
          },
        ]),
      ),
      http.get("/api/chat/history", async () => HttpResponse.json([])),
    );

    await useDiagnosisStore.getState().bootstrapSession("sess-latency-001");

    const state = useDiagnosisStore.getState();
    expect(state.topologyContext?.summary).toBe("new topology");
    expect(state.topologyContext?.roots).toEqual(["service:new-root"]);
    expect(state.topologyContext?.affected_count).toBe(2);
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

  it("appends canary batch progress messages in order and then observation summary", async () => {
    await useDiagnosisStore.getState().bootstrapSession(diagnosisSession.session_id);

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "remediation_progress",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-04-15T09:00:01Z",
      data: {
        stage: "canary_batch_started",
        batch: "canary-1",
        message: "灰度批次 1/2 开始，覆盖目标 proc:ls_demo_a",
      },
    });
    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "remediation_progress",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-04-15T09:00:10Z",
      data: {
        stage: "canary_batch_completed",
        batch: "canary-1",
        message: "灰度批次 1/2 完成，覆盖目标 proc:ls_demo_a",
      },
    });
    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "observation_result",
      session_id: diagnosisSession.session_id,
      timestamp: "2026-04-15T09:01:00Z",
      data: {
        alert_cleared: true,
        metrics_improved: true,
        baseline_alert: { status: "firing" },
        post_alert: { status: "resolved" },
      },
    });

    const state = useDiagnosisStore.getState();
    const latestMessages = state.messages.slice(-3).map((item) => item.content);
    expect(latestMessages[0]).toBe("灰度批次 1/2 开始，覆盖目标 proc:ls_demo_a");
    expect(latestMessages[1]).toBe("灰度批次 1/2 完成，覆盖目标 proc:ls_demo_a");
    expect(latestMessages[2]).toContain("观察结果：alert_cleared=true，metrics_improved=true，告警状态 firing -> resolved");
  });
});
