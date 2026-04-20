import { describe, expect, it } from "vitest";

import type { DiagnosisLocalAuditRecord, DiagnosisSession, SessionEvent } from "../api/types";
import { buildDiagnosisModifiedReportView, mapDiagnosisModifiedStage } from "./diagnosisModifiedReportModel";

function createSession(status = "approval_required"): DiagnosisSession {
  return {
    session_id: "sess-report-model",
    alert: {
      alert_name: "Latency spike",
      severity: "critical",
      labels: { service: "auth-svc" },
      annotations: { summary: "p95 spike" },
      starts_at: "2026-04-08T10:00:00.000Z",
      fingerprint: "fp-report-model",
      status: "firing",
      source: "alertmanager",
    },
    status,
    duration_seconds: 420,
    outcome: status === "resolved" ? "resolved" : undefined,
    diagnosis_result: {
      root_cause: "Redis connection saturation",
      root_cause_layer: "service",
      root_cause_entities: ["redis-primary", "auth-svc"],
      confidence: 0.86,
      hypotheses: [
        {
          description: "Redis timeout amplifies auth retry pressure",
          status: "confirmed",
          confidence: 0.86,
          evidence_for: ["Redis timeout observed"],
          evidence_against: [],
        },
      ],
      impact_summary: "Auth login latency spikes and partial failures.",
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
          evidence_summary: "Redis timeout aligns with alert window.",
        },
        {
          rank: 2,
          root_cause: "Downstream database wait queue",
          root_cause_layer: "service",
          root_cause_entities: ["orders-db"],
          confidence: 0.54,
          evidence_summary: "Database wait queue increased after retries.",
        },
        {
          rank: 3,
          root_cause: "Ingress throttling",
          root_cause_layer: "network",
          root_cause_entities: ["edge-gateway"],
          confidence: 0.32,
          evidence_summary: "Ingress latency rose later in the incident.",
        },
        {
          rank: 4,
          root_cause: "Node pressure",
          root_cause_layer: "hardware",
          root_cause_entities: ["node-17"],
          confidence: 0.18,
          evidence_summary: "Node pressure is lower-confidence background noise.",
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
          success_criteria: [{ metric: "error_rate", operator: "<", value: 1 }],
        },
        estimated_impact: "Limited canary execution before wider rollout.",
        confidence: 0.79,
        priority: "P1",
        safety_level: "guarded",
      },
    },
  };
}

describe("mapDiagnosisModifiedStage", () => {
  it("maps session statuses into lifecycle labels", () => {
    expect(mapDiagnosisModifiedStage("diagnosing").label).toBe("诊断中");
    expect(mapDiagnosisModifiedStage("diagnosed").label).toBe("已诊断");
    expect(mapDiagnosisModifiedStage("approval_required").label).toBe("待审批");
    expect(mapDiagnosisModifiedStage("approved").label).toBe("已批准");
    expect(mapDiagnosisModifiedStage("remediating").label).toBe("修复中");
    expect(mapDiagnosisModifiedStage("validating").label).toBe("验证中");
    expect(mapDiagnosisModifiedStage("resolved").label).toBe("已解决");
    expect(mapDiagnosisModifiedStage("closed").label).toBe("已关闭");
    expect(mapDiagnosisModifiedStage("failed").label).toBe("失败");
    expect(mapDiagnosisModifiedStage("escalated").label).toBe("已升级");
    expect(mapDiagnosisModifiedStage("rejected").label).toBe("已驳回");
  });
});

describe("buildDiagnosisModifiedReportView", () => {
  it("builds the overview from existing session fields", () => {
    const view = buildDiagnosisModifiedReportView({
      session: createSession("validating"),
      timeline: [
        {
          id: "message-1",
          kind: "message",
          role: "assistant",
          content: "done",
          timestamp: "2026-04-08T10:12:00.000Z",
        },
      ],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.overview.sessionId).toBe("sess-report-model");
    expect(view.overview.alertName).toBe("Latency spike");
    expect(view.overview.service).toBe("auth-svc");
    expect(view.overview.status.label).toBe("验证中");
    expect(view.overview.updatedAt).toBe("2026-04-08T10:12:00.000Z");
    expect(view.overview.subtitle).toBe("Auth login latency spikes and partial failures.");
  });

  it("derives diagnosis context from root-cause entities and affected services", () => {
    const view = buildDiagnosisModifiedReportView({
      session: createSession(),
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.state).toBe("ready");
    expect(view.context.problemNodes.map((node) => node.label)).toEqual(["redis-primary", "auth-svc"]);
    expect(view.context.affectedNodes.map((node) => node.label)).toEqual(["login-api"]);
    expect(view.context.graph.nodes).toHaveLength(3);
    expect(view.context.graph.edges).toEqual([
      {
        id: "context-edge-redis-primary-login-api",
        sourceId: "redis-primary",
        targetId: "login-api",
        label: "影响",
      },
      {
        id: "context-edge-auth-svc-login-api",
        sourceId: "auth-svc",
        targetId: "login-api",
        label: "影响",
      },
    ]);
  });

  it("prefers ranked candidates from the contract and caps them at three", () => {
    const view = buildDiagnosisModifiedReportView({
      session: createSession(),
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.candidateChanges).toHaveLength(3);
    expect(view.candidateChanges[0]?.title).toBe("Redis connection saturation");
    expect(view.candidateChanges[0]?.statusLabel).toBe("当前根因");
    expect(view.candidateChanges[2]?.title).toBe("Ingress throttling");
    expect(view.rootCauseReady).toBe(true);
    expect(view.rootCause.state).toBe("ready");
  });

  it("compresses execution and feedback from existing event and audit sources", () => {
    const events: SessionEvent[] = [
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-report-model",
        timestamp: "2026-04-08T10:05:00.000Z",
        data: {
          stage: "canary_progress",
          progress: 40,
          progress_label: "Canary 40%",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-report-model",
        timestamp: "2026-04-08T10:07:00.000Z",
        data: {
          stage: "alert_recovered",
          message: "Primary alert has recovered.",
        },
      },
    ];
    const localAuditRecords: DiagnosisLocalAuditRecord[] = [
      {
        id: "audit-approval",
        sessionId: "sess-report-model",
        eventKind: "approval_result",
        source: "local_audit",
        dedupeKey: "approval-v2",
        timestamp: "2026-04-08T10:03:00.000Z",
        summary: "[系统] 已确认审批，准备进入执行",
        details: ["审批动作：同意，通过执行"],
        statusTone: "success",
      },
    ];

    const view = buildDiagnosisModifiedReportView({
      session: createSession("remediating"),
      timeline: [],
      candidates: [],
      events,
      localAuditRecords,
    });

    expect(view.execution.title).toContain("灰度");
    expect(view.execution.highlights.some((item) => item.includes("10%"))).toBe(true);
    expect(view.feedback.length).toBe(2);
    expect(view.feedback[0]?.summary).toContain("Primary alert has recovered");
    expect(view.nextAction.mode).toBe("executing");
    expect(view.remediation.state).toBe("ready");
    expect(view.remediation.steps).toHaveLength(2);
  });

  it("keeps overview visible and returns loading state for incomplete report sections", () => {
    const session = createSession("diagnosing");
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.overview.sessionId).toBe("sess-report-model");
    expect(view.overview.status.label).toBe("诊断中");
    expect(view.rootCauseReady).toBe(false);
    expect(view.context.state).toBe("loading");
    expect(view.rootCause.state).toBe("loading");
    expect(view.remediation.state).toBe("loading");
    expect(view.conclusion.title).toBe("等待形成明确结论");
    expect(view.candidateChanges).toHaveLength(0);
  });
});
