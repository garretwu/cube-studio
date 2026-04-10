import { beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";

import type { DiagnosisSession, WSEvent } from "../api/types";
import { server } from "../test/server";
import { useDiagnosisStore } from "./diagnosisStore";

const testSessionId = "sess-latency-001";

const testChatMessages = [
  {
    id: "chat-1",
    role: "assistant" as const,
    content: "我已经带入当前 AIDC 拓扑与最新告警，可以继续帮你诊断问题或生成修复计划。",
    created_at: "2026-03-18T12:04:00Z",
  },
];

function buildTestSession(overrides: Partial<DiagnosisSession> = {}): DiagnosisSession {
  return {
    session_id: testSessionId,
    alert: {
      alert_name: "VLLM 延迟过高",
      severity: "critical",
      labels: { service: "vllm", instance: "vllm-0", aidc: "aidc-001" },
      annotations: { summary: "p95 延迟已超过 600ms", entity: "svc-vllm" },
      starts_at: "2026-03-18T12:00:00Z",
      fingerprint: "fp-001",
      status: "firing",
      source: "alertmanager",
    },
    status: "remediating",
    re_diagnosis_round: 1,
    duration_seconds: 182,
    outcome: "proposed_fix_ready",
    diagnosis_result: {
      root_cause: "异常基准测试进程导致 GPU 资源争用",
      root_cause_layer: "hardware",
      root_cause_entities: ["gpu-01", "node-gpu-01"],
      confidence: 0.93,
      recommended_fix: {
        plan_id: "plan-rollback-01-v3",
        root_cause: "异常基准测试进程导致 GPU 资源争用",
        description: "先排空 10% 金丝雀分片，再终止 gpu-burn 进程，最后确认 vLLM p95 与 GPU 利用率恢复正常。",
        steps: [],
        estimated_impact: "业务影响较低，灰度阶段仅影响 10% 推理分片，并保留快速回滚能力。",
        confidence: 0.93,
        priority: "P1" as const,
        canary: { enabled: true, target_percentage: 0.1, monitor_duration: 120, success_criteria: [] },
        safety_level: "high",
      },
      hypotheses: [
        {
          description: "RoCE 网络拥塞导致链路退化",
          status: "eliminated",
          evidence_for: ["ECN 计数器曾短暂升高"],
          evidence_against: ["交换机队列深度维持正常"],
          confidence: 0.28,
        },
        {
          description: "异常进程占满 GPU 资源",
          status: "confirmed",
          evidence_for: ["DCGM 进程列表出现 gpu-burn", "利用率持续锁定在 92%"],
          evidence_against: [],
          confidence: 0.93,
        },
      ],
      impact_summary: "单个推理分片持续饱和，导致整条服务链路上的 p95 延迟被放大。",
      affected_services: ["vllm-latency", "chat-serving"],
      triage_priority: "P1",
      diagnosis_certainty: "confirmed",
    },
    trace: {
      steps: [
        {
          step: 1,
          timestamp: "2026-03-18T12:00:12Z",
          thought: "先把延迟告警与当前拓扑影响半径关联起来，判断是否存在局部资源热点。",
          action_type: "tool_call",
          tool_name: "ontology.get_blast_radius",
          tool_params: { entity_id: "svc-vllm" },
          confidence: 0.74,
        },
        {
          tool: "prometheus_query",
          params: { query: "gpu_utilization{node='node-gpu-01'}" },
          result: { value: 0.92, trend: "上升" },
          timestamp: "2026-03-18T12:00:32Z",
        },
        {
          step: 2,
          timestamp: "2026-03-18T12:00:48Z",
          thought: "温度和利用率信号都指向本地 GPU 资源争用，而不是网络丢包或拥塞。",
          action_type: "conclude",
          confidence: 0.93,
        },
      ],
    },
    bootstrap: {
      session_name: "3月18日推理变慢",
      started_at: "2026-03-18T12:00:12Z",
      related_alerts: {
        count: 2,
        items: [
          { id: "fp-001", alert_name: "VLLM 延迟过高", severity: "critical", source_entity: "svc-vllm", starts_at: "2026-03-18T12:00:00Z", summary: "p95 延迟已超过 600ms" },
          { id: "fp-002", alert_name: "GPU 温度偏高", severity: "warning", source_entity: "node-gpu-01", starts_at: "2026-03-18T11:57:00Z", summary: "GPU 温度持续高于目标阈值" },
        ],
      },
      impact: {
        object_count: 2,
        service_count: 2,
        affected_entities: ["gpu-01", "node-gpu-01"],
        affected_services: ["vllm-latency", "chat-serving"],
        blast_radius_summary: "影响集中在单个热点节点，但已放大到整条推理链路的 p95 延迟。",
      },
    },
    ...overrides,
  };
}

async function waitUntil(assertion: () => void, timeoutMs = 1200): Promise<void> {
  const startedAt = Date.now();
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try {
      assertion();
      return;
    } catch (error) {
      if (Date.now() - startedAt >= timeoutMs) {
        throw error;
      }
      await new Promise((resolve) => {
        setTimeout(resolve, 30);
      });
    }
  }
}

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

    expect(useDiagnosisStore.getState().messages).toHaveLength(testChatMessages.length + 2);
    expect(useDiagnosisStore.getState().activeSessionId).toBe(testSessionId);

    server.use(
      http.get("/api/sessions/:sessionId", async ({ params }) =>
        HttpResponse.json({
          ...buildTestSession(),
          session_id: String(params.sessionId ?? "sess-latency-002"),
        }),
      ),
    );

    await useDiagnosisStore.getState().bootstrapSession("sess-latency-002");

    expect(useDiagnosisStore.getState().session?.session_id).toBe("sess-latency-002");
    expect(useDiagnosisStore.getState().activeSessionId).toBe("sess-latency-002");
    expect(useDiagnosisStore.getState().messages).toEqual(testChatMessages);
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
      session_id: testSessionId,
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
      session_id: testSessionId,
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

  it("deduplicates websocket events by event_id before appending trace entries", async () => {
    await useDiagnosisStore.getState().bootstrapSession();

    const baselineTraceCount = useDiagnosisStore.getState().session?.trace?.steps.length ?? 0;
    const baselineEventCount = useDiagnosisStore.getState().events.length;

    const thinkingEvent: WSEvent = {
      schema_version: "1",
      type: "thinking_step",
      session_id: testSessionId,
      timestamp: "2026-03-18T12:07:00Z",
      data: {
        event_id: "evt-dedupe-1",
        step: 4,
        thought: "Recheck the same metric snapshot",
        action_type: "tool_call",
        tool_name: "metrics.query",
      },
    };

    const resultEvent: WSEvent = {
      schema_version: "1",
      type: "tool_result",
      session_id: testSessionId,
      timestamp: "2026-03-18T12:07:05Z",
      data: {
        event_id: "evt-dedupe-2",
        tool_name: "metrics.query",
        result: {
          value: 0.95,
        },
      },
    };

    useDiagnosisStore.getState().applyEvent(thinkingEvent);
    useDiagnosisStore.getState().applyEvent(thinkingEvent);
    useDiagnosisStore.getState().applyEvent(resultEvent);
    useDiagnosisStore.getState().applyEvent(resultEvent);

    const state = useDiagnosisStore.getState();

    expect(state.events).toHaveLength(baselineEventCount + 2);
    expect(state.session?.trace?.steps).toHaveLength(baselineTraceCount + 2);
  });

  it("deduplicates websocket events without event_id using type/timestamp/payload identity", async () => {
    await useDiagnosisStore.getState().bootstrapSession();

    const baselineTraceCount = useDiagnosisStore.getState().session?.trace?.steps.length ?? 0;
    const baselineEventCount = useDiagnosisStore.getState().events.length;

    const thinkingEvent: WSEvent = {
      schema_version: "1",
      type: "thinking_step",
      session_id: testSessionId,
      timestamp: "2026-03-18T12:08:00Z",
      data: {
        step: 5,
        thought: "Check whether the same fallback identity dedupes",
        action_type: "conclude",
      },
    };

    useDiagnosisStore.getState().applyEvent(thinkingEvent);
    useDiagnosisStore.getState().applyEvent({
      ...thinkingEvent,
      data: { ...thinkingEvent.data },
    });

    const state = useDiagnosisStore.getState();

    expect(state.events).toHaveLength(baselineEventCount + 1);
    expect(state.session?.trace?.steps).toHaveLength(baselineTraceCount + 1);
  });

  it("updates session status immediately when diagnosis_result arrives", async () => {
    await useDiagnosisStore.getState().bootstrapSession(testSessionId);
    useDiagnosisStore.setState((state) => ({
      session: state.session
        ? {
            ...state.session,
            status: "diagnosing",
            diagnosis_result: null,
          }
        : state.session,
    }));

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "diagnosis_result",
      session_id: testSessionId,
      timestamp: "2026-04-03T14:00:00Z",
      data: {
        ...(buildTestSession().diagnosis_result ?? {}),
        recommended_fix: null,
      },
    });

    await waitUntil(() => {
      expect(useDiagnosisStore.getState().session?.status).toBe("diagnosed");
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
            ...buildTestSession(),
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
    const sessionId = testSessionId;
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
          ...buildTestSession(),
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

  it("marks the session resolved and summarizes alert status when observation_result clears the alert", async () => {
    await useDiagnosisStore.getState().bootstrapSession(testSessionId);

    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "observation_result",
      session_id: testSessionId,
      timestamp: "2026-04-03T14:10:00Z",
      data: {
        alert_cleared: true,
        metrics_improved: true,
        baseline_alert: { status: "firing" },
        post_alert: { status: "resolved" },
      },
    });

    const state = useDiagnosisStore.getState();
    expect(state.session?.status).toBe("resolved");
    expect(state.messages.at(-1)?.content).toContain("告警状态 firing -> resolved");
  });

  it("extracts remediation plan from ranked_candidates when top-level plan is empty", async () => {
    const candidatePlan = {
      plan_id: "candidate-plan-v2",
      root_cause: "gpu contention",
      description: "candidate fallback plan",
      steps: [
        {
          step_id: 1,
          description: "kill gpu-burn",
          tool: "shell_command",
          params: { command: "pkill -f gpu-burn" },
          verification: { method: "wait", wait_seconds: 2 },
          timeout: 60,
        },
      ],
      estimated_impact: "minor",
      confidence: 0.86,
      priority: "P1",
    };
    server.use(
      http.get("/api/sessions/:sessionId", async ({ params }) =>
        HttpResponse.json({
          ...buildTestSession(),
          session_id: String(params.sessionId ?? testSessionId),
          status: "approval_required",
          diagnosis_result: {
            ...(buildTestSession().diagnosis_result ?? {}),
            recommended_fix: null,
            ranked_candidates: [
              {
                rank: 1,
                root_cause: "gpu contention",
                root_cause_layer: "service",
                confidence: 0.86,
                recommended_fix: candidatePlan,
              },
            ],
          },
        }),
      ),
      http.get("/api/sessions/:sessionId/events", async () => HttpResponse.json([])),
    );

    await useDiagnosisStore.getState().bootstrapSession(testSessionId);
    const state = useDiagnosisStore.getState();
    expect(state.hasPlan).toBe(true);
    expect(state.planMissingReason).toBeUndefined();
    expect(state.currentPlanVersion).toBe(2);
  });

  it("backfills session snapshot after approval_required event to avoid missing final diagnosis payload", async () => {
    const sessionId = "sess-backfill-001";
    const repairedPlan = {
      plan_id: "plan-backfill-v1",
      root_cause: "gpu contention",
      description: "backfilled plan",
      steps: [],
      estimated_impact: "minor",
      confidence: 0.8,
      priority: "P1",
    };
    let capturedAfter = "";
    let sessionFetchCount = 0;
    server.use(
      http.get("/api/sessions/:sessionId", async ({ params }) => {
        sessionFetchCount += 1;
        const withPlan = sessionFetchCount > 1;
        return HttpResponse.json({
          ...buildTestSession(),
          session_id: String(params.sessionId ?? sessionId),
          status: "approval_required",
          diagnosis_result: {
            ...(buildTestSession().diagnosis_result ?? {}),
            recommended_fix: withPlan ? repairedPlan : null,
          },
        });
      }),
      http.get("/api/sessions/:sessionId/events", async ({ request, params }) => {
        const url = new URL(request.url);
        capturedAfter = url.searchParams.get("after") ?? "";
        return HttpResponse.json([
          {
            schema_version: "1",
            type: "diagnosis_result",
            session_id: String(params.sessionId ?? sessionId),
            timestamp: "2026-04-03T12:00:00Z",
            data: {
              event_id: "evt-backfill-200",
              ...(buildTestSession().diagnosis_result ?? {}),
              recommended_fix: repairedPlan,
            },
          },
        ]);
      }),
    );

    await useDiagnosisStore.getState().bootstrapSession(sessionId);
    useDiagnosisStore.getState().applyEvent({
      schema_version: "1",
      type: "approval_required",
      session_id: sessionId,
      timestamp: "2026-04-03T11:59:59Z",
      data: {
        plan_id: "plan-backfill-v1",
        plan_version: 1,
        event_id: "evt-backfill-100",
      },
    });

    await waitUntil(() => {
      expect(useDiagnosisStore.getState().session?.diagnosis_result?.recommended_fix?.plan_id).toBe("plan-backfill-v1");
    });
    expect(sessionFetchCount).toBeGreaterThan(1);
    expect(capturedAfter).toBe("evt-backfill-100");
  });
});
