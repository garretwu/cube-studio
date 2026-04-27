import { describe, expect, it } from "vitest";

import type { DiagnosisLocalAuditRecord, DiagnosisSession, SessionEvent } from "../api/types";
import type { DiagnosisModifiedCandidateView } from "./diagnosisModifiedModel";
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

function createCandidateSnapshots(): Array<{
  id: string;
  timestamp: string;
  candidates: DiagnosisModifiedCandidateView[];
}> {
  return [
    {
      id: "candidate-snapshot-1",
      timestamp: "2026-04-08T10:02:00.000Z",
      candidates: [
        {
          id: "candidate-redis-early",
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
          isPrimary: false,
        },
        {
          id: "candidate-db-early",
          title: "Downstream database wait queue",
          summary: "Database wait queue increased after retries.",
          confidence: 0.34,
          confidenceLabel: "34%",
          statusLabel: "候选 2",
          statusTone: "neutral",
          evidenceFor: ["Database queue rose"],
          evidenceAgainst: [],
          entities: ["orders-db"],
          rank: 2,
          evidenceSummary: "Database queue increased after retries.",
          isPrimary: false,
        },
      ],
    },
    {
      id: "candidate-snapshot-2",
      timestamp: "2026-04-08T10:05:00.000Z",
      candidates: [
        {
          id: "candidate-redis-final",
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
        {
          id: "candidate-db-final",
          title: "Downstream database wait queue",
          summary: "Database wait queue is now secondary.",
          confidence: 0.41,
          confidenceLabel: "41%",
          statusLabel: "候选 2",
          statusTone: "neutral",
          evidenceFor: ["Database queue rose"],
          evidenceAgainst: ["Queue increase trails the Redis timeout"],
          entities: ["orders-db"],
          rank: 2,
          evidenceSummary: "Database queue trails the Redis timeout pattern.",
          distinguishingVerification: "Check whether queue depth stays high after Redis recovers.",
          isPrimary: false,
        },
      ],
    },
  ];
}

describe("mapDiagnosisModifiedStage", () => {
  it("maps session statuses into lifecycle labels", () => {
    expect(mapDiagnosisModifiedStage(undefined).label).toBe("未开始");
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
  it("activates the remediation stage only after a plan is available", () => {
    const candidateSnapshots = createCandidateSnapshots();

    const view = buildDiagnosisModifiedReportView({
      session: createSession("approval_required"),
      timeline: [
        {
          id: "message-topology-context",
          kind: "message",
          role: "assistant",
          content:
            '[HumanMessage - topology_context]\nTopology context:\n{"roots":["redis-primary"],"affected_count":1,"affected_entities":[{"id":"service:auth-svc","name":"auth-svc"}],"summary":"topology blast radius: redis-primary impacts auth-svc"}',
          timestamp: "2026-04-08T10:01:00.000Z",
        },
      ],
      candidates: candidateSnapshots[1]?.candidates ?? [],
      candidateSnapshots,
      events: [],
      localAuditRecords: [],
    });

    expect(view.progress.activeStepId).toBe("remediation");
    expect(view.progress.steps.map((step) => `${step.id}:${step.status}`)).toEqual([
      "context:completed",
      "hypotheses:completed",
      "verification:completed",
      "confidence:completed",
      "remediation:active",
    ]);
    expect(view.remediation.state).toBe("ready");
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
    expect(view.overview.title).toBe("04-08Latency spike诊断报告");
    expect(view.overview.alertName).toBe("Latency spike");
    expect(view.overview.service).toBe("auth-svc");
    expect(view.overview.status.label).toBe("验证中");
    expect(view.overview.updatedAt).toBe("2026-04-08T10:12:00.000Z");
    expect(view.overview.subtitle).toBe("Auth login latency spikes and partial failures.");
  });

  it("renders alert subject only when no direct topology context exists", () => {
    const view = buildDiagnosisModifiedReportView({
      session: createSession(),
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.state).toBe("ready");
    expect(view.context.problemNodes.map((node) => node.label)).toEqual(["auth-svc"]);
    expect(view.context.affectedNodes).toEqual([]);
    expect(view.context.graph.nodes).toHaveLength(1);
    expect(view.context.graph.edges).toEqual([]);
    expect(view.context.topologyEmptyReason).toBe("no_direct_relations");
  });

  it("does not use timeline topology fallback in direct-only context mode", () => {
    const session = createSession("diagnosing");
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [
        {
          id: "message-context-built",
          kind: "message",
          role: "assistant",
          content:
            '构建上下文完成，拓扑信息如下：\\n{"roots":["worker-03"],"affected_count":1,"affected_entities":[{"id":"service:auth-svc","name":"auth-svc"}],"summary":"worker-03 impacts auth-svc"}',
          timestamp: "2026-04-08T10:01:00.000Z",
        },
      ],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.state).toBe("ready");
    expect(view.context.problemNodes.map((node) => node.label)).toContain("auth-svc");
    expect(view.context.affectedNodes).toEqual([]);
    expect(view.context.graph.edges).toEqual([]);
    expect(view.context.topologyEmptyReason).toBe("no_direct_relations");
  });

  it("prefers diagnosis_started topologyContext over timeline-parsed fallback", () => {
    const view = buildDiagnosisModifiedReportView({
      session: createSession("diagnosing"),
      topologyContext: {
        roots: ["gpu:0"],
        affected_count: 2,
        affected_entities: [
          { id: "node:worker-03", type: "node", name: "worker-03" },
          { id: "bmc:worker-03-bmc", type: "bmc", name: "worker-03-bmc" },
        ],
        summary: "GPU context from diagnosis_started",
      },
      timeline: [
        {
          id: "message-context-fallback",
          kind: "message",
          role: "assistant",
          content:
            'Topology context:\n{"roots":["service:auth-svc"],"affected_count":1,"affected_entities":[{"id":"pod:auth-1","name":"auth-1"}],"summary":"fallback timeline context"}',
          timestamp: "2026-04-08T10:01:00.000Z",
        },
      ],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.summary).toContain("GPU context from diagnosis_started");
    expect(view.context.problemNodes.map((node) => node.label)).toContain("0");
    expect(view.context.graph.nodes.map((node) => node.label)).not.toContain("auth-1");
  });

  it("adds alert-semantic topology links for GPU temperature alerts", () => {
    const session = createSession("diagnosing");
    session.alert.alert_name = "GPUTemperatureHigh";
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      topologyContext: {
        roots: ["gpu:0"],
        affected_count: 2,
        affected_entities: [
          { id: "node:worker-03", type: "node", name: "worker-03" },
          { id: "bmc:worker-03-bmc", type: "bmc", name: "worker-03-bmc" },
        ],
        summary: "gpu -> node -> bmc",
      },
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    const labels = view.context.graph.edges.map((edge) => edge.label);
    expect(labels).toContain("所在节点");
    expect(labels).toContain("硬件管理");
  });

  it("maps TTFT alerts to one-hop relations with service/pod/node priority", () => {
    const session = createSession("diagnosing");
    session.alert.alert_name = "TTFTLatencyHigh";
    session.alert.labels = { pod: "service/qwen3-32b-fp8-202602261-6778dcf4d8-6hmld" };
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      topologyContext: {
        roots: ["pod:service/qwen3-32b-fp8-202602261-6778dcf4d8-6hmld"],
        affected_count: 3,
        affected_entities: [
          { id: "node:wj-lab-cpt-03", type: "node", name: "wj-lab-cpt-03" },
          { id: "service:qwen3-32b-fp8-202602261", type: "service", name: "qwen3-32b-fp8-202602261" },
          { id: "gpu:1", type: "gpu", name: "GPU 1" },
        ],
        summary: "ttft pod/node/service topology",
      },
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.problemNodes.map((node) => node.label)).toEqual(["service/qwen3-32b-fp8-202602261-6778dcf4d8-6hmld"]);
    expect(view.context.affectedNodes.map((node) => node.label)).toEqual([
      "wj-lab-cpt-03",
      "qwen3-32b-fp8-202602261",
      "1",
    ]);
    expect(view.context.graph.edges.map((edge) => edge.label)).toEqual(["运行于", "隶属服务", "关联"]);
  });

  it("keeps GPU and process neighbors for TTFT context instead of hard-filtering", () => {
    const session = createSession("diagnosing");
    session.alert.alert_name = "AIServiceTTFTP99High";
    session.alert.labels = { pod: "qwen-main-0" };
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      topologyContext: {
        roots: ["pod:service:qwen-main-0"],
        affected_count: 4,
        affected_entities: [
          { id: "node:worker-03", type: "node", name: "worker-03" },
          { id: "service:qwen-main", type: "service", name: "qwen-main" },
          { id: "gpu:worker-03:0", type: "gpu", name: "GPU 0" },
          { id: "proc:gpu-burn", type: "process", name: "gpu-burn" },
        ],
        summary: "ttft with gpu/process neighbors",
      },
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.affectedNodes.map((node) => node.label)).toEqual([
      "worker-03",
      "qwen-main",
      "0",
      "gpu-burn",
    ]);
    expect(view.context.graph.edges).toHaveLength(4);
  });

  it("keeps unknown affected entity ids as generic neighbors in TTFT context", () => {
    const session = createSession("diagnosing");
    session.alert.alert_name = "AIServiceTTFTP99High";
    session.alert.labels = { service: "auth-svc" };
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      topologyContext: {
        roots: ["service:auth-svc"],
        affected_count: 1,
        affected_entities: [{ id: "mystery-target", name: "mystery-target" }],
        summary: "ttft with unknown id",
      },
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.graph.nodes.some((node) => node.id === "entity:mystery-target")).toBe(true);
    expect(view.context.graph.edges).toHaveLength(1);
  });

  it("does not merge same-label entities when types differ", () => {
    const session = createSession("diagnosing");
    session.alert.alert_name = "AIServiceTTFTP99High";
    session.alert.labels = { service: "shared" };
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      topologyContext: {
        roots: ["service:shared"],
        affected_count: 2,
        affected_entities: [
          { id: "pod:service:shared", name: "shared" },
          { id: "service:shared-sidecar", name: "shared-sidecar" },
        ],
        summary: "same label different types",
      },
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    const ids = view.context.graph.nodes.map((node) => node.id);
    expect(ids).toContain("service:shared");
    expect(ids).toContain("pod:service:shared");
    expect(view.context.graph.edges).toHaveLength(2);
  });

  it("adds fallback one-hop edges when strict semantic filtering drops all neighbors", () => {
    const session = createSession("diagnosing");
    session.alert.alert_name = "GPUTemperatureHigh";
    session.alert.labels = { gpu: "0" };
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      topologyContext: {
        roots: ["gpu:0"],
        affected_count: 1,
        affected_entities: [{ id: "service:auth-svc", name: "auth-svc" }],
        summary: "fallback topology",
      },
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.graph.edges).toHaveLength(1);
    expect(view.context.graph.edges[0]?.label).toBe("关联");
    expect(view.context.affectedNodes.map((node) => node.id)).toContain("service:auth-svc");
  });

  it("keeps alert subject node when topology context has no direct neighbors", () => {
    const session = createSession("diagnosing");
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      topologyContext: {
        roots: [],
        affected_count: 0,
        affected_entities: [],
        summary: "no linked entities",
      },
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.context.state).toBe("ready");
    expect(view.context.problemNodes.map((node) => node.label)).toEqual(["auth-svc"]);
    expect(view.context.graph.nodes).toHaveLength(1);
    expect(view.context.graph.edges).toEqual([]);
    expect(view.context.topologyEmptyReason).toBe("no_direct_relations");
  });

  it("sanitizes tool-call markup from analysis overview title", () => {
    const session = createSession("approval_required");
    if (!session.diagnosis_result) {
      throw new Error("expected diagnosis result");
    }
    session.diagnosis_result.root_cause =
      '[TOOL_CALL] {tool => "ssh.run_command"} [/TOOL_CALL] GPU 温度异常来自风扇策略偏移';

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.overview.title).toContain("Latency spike");
    expect(view.overview.title).not.toContain("[TOOL_CALL]");
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

  it("keeps the full post-approval session feedback chain visible through closure", () => {
    const session = createSession("closed");
    const localAuditRecords: DiagnosisLocalAuditRecord[] = [
      {
        id: "audit-approval-chain",
        sessionId: "sess-report-model",
        eventKind: "approval_result",
        source: "local_audit",
        dedupeKey: "approval-chain-v1",
        timestamp: "2026-04-08T10:03:00.000Z",
        summary: "[system] approval recorded",
        details: ["Execute 10% canary first, then observe Redis timeout recovery."],
        statusTone: "success",
      },
    ];
    const events: SessionEvent[] = [
      {
        schema_version: "1",
        type: "observation_result",
        session_id: "sess-report-model",
        timestamp: "2026-04-08T10:04:00.000Z",
        data: {
          stage: "observation_result",
          message: "Key latency and error metrics are trending back to baseline.",
          progress_label: "p95 latency and error rate returned to guarded thresholds.",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-report-model",
        timestamp: "2026-04-08T10:05:00.000Z",
        data: {
          stage: "alert_recovered",
          message: "Primary alert recovered after the guarded canary.",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-report-model",
        timestamp: "2026-04-08T10:06:00.000Z",
        data: {
          stage: "session_closed",
          message: "Session closed after verification.",
        },
      },
    ];

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [],
      candidates: [],
      events,
      localAuditRecords,
    });

    expect(view.feedback).toHaveLength(4);
    expect(view.feedback.map((item) => item.label)).toEqual([
      "会话关闭",
      "告警恢复",
      "指标反馈",
      "审批结论",
    ]);
    expect(view.feedback[3]?.summary).toContain("approval recorded");
  });

  it("prefers the remediation plan attached to the confirmed ranked candidate", () => {
    const session = createSession("approval_required");
    if (!session.diagnosis_result?.ranked_candidates?.[0]) {
      throw new Error("expected ranked candidate");
    }

    session.diagnosis_result.recommended_fix = {
      plan_id: "plan-fallback-top-level",
      root_cause: "Shared fallback plan",
      description: "Fallback plan that should not win when the candidate has its own remediation.",
      steps: [
        {
          step_id: 1,
          description: "Fallback mitigation",
          tool: "fallback_tool",
          params: { target: "fallback" },
          verification: { method: "wait", wait_seconds: 30 },
          timeout: 300,
        },
      ],
      estimated_impact: "Fallback impact",
      confidence: 0.4,
      priority: "P2",
      safety_level: "fallback",
    };

    session.diagnosis_result.ranked_candidates[0].recommended_fix = {
      plan_id: "plan-ranked-redis",
      root_cause: "Redis connection saturation",
      description: "Candidate-specific Redis mitigation should be bound to the confirmed root cause.",
      steps: [
        {
          step_id: 1,
          description: "Drain Redis-heavy traffic from the hot shard",
          tool: "traffic_shift",
          params: { shard: "redis-primary" },
          verification: { method: "promql", query: "rate(redis_timeout_total[5m])" },
          timeout: 300,
        },
      ],
      estimated_impact: "Candidate-specific impact",
      confidence: 0.91,
      priority: "P1",
      safety_level: "guarded",
    };

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.remediation.state).toBe("ready");
    expect(view.remediation.detail).toContain("Candidate-specific Redis mitigation");
    expect(view.remediation.steps).toHaveLength(1);
    expect(view.remediation.steps[0]?.title).toContain("Drain Redis-heavy traffic");
  });

  it("builds progressive report stages from context, candidate, verification, and confidence signals", () => {
    const candidateSnapshots = createCandidateSnapshots();
    const session = createSession("diagnosing");
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [
        {
          id: "message-topology-context",
          kind: "message",
          role: "assistant",
          content:
            '[HumanMessage - topology_context]\nTopology context:\n{"roots":["redis-primary"],"affected_count":2,"affected_entities":[{"id":"service:auth-svc","name":"auth-svc"},{"id":"service:login-api","name":"login-api"}],"summary":"topology blast radius: redis-primary impacts auth-svc and login-api"}',
          timestamp: "2026-04-08T10:01:00.000Z",
        },
        {
          id: "tool-redis-metrics",
          kind: "tool",
          toolName: "query_service_metrics",
          params: { service: "auth-svc", window: "5m" },
          timestamp: "2026-04-08T10:04:00.000Z",
          status: "success",
          summaryLines: ["redis_timeout: +240%", "retry_rate: +180%"],
          rawResult: { redis_timeout: "240%", retry_rate: "180%" },
        },
        {
          id: "message-next-validation",
          kind: "message",
          role: "assistant",
          content: "Next action: validate Redis saturation against retry amplification before rollout.",
          timestamp: "2026-04-08T10:04:30.000Z",
          label: "Next action",
        },
      ],
      candidates: candidateSnapshots[1]?.candidates ?? [],
      candidateSnapshots,
      events: [],
      localAuditRecords: [],
    });

    expect(view.progress.activeStepId).toBe("confidence");
    expect(view.progress.steps.map((step) => `${step.id}:${step.status}`)).toEqual([
      "context:completed",
      "hypotheses:completed",
      "verification:completed",
      "confidence:active",
      "remediation:pending",
    ]);
    expect(view.hypotheses.state).toBe("ready");
    expect(view.hypotheses.items).toHaveLength(2);
    expect(view.hypotheses.summary).toBe("已选中 2 个候选假设。");
    expect(view.hypotheses.description).toContain("每个候选假设展示证据与置信度变化");
    expect(view.hypotheses.items[0]?.summary).toContain("Redis timeout and retry amplification");
    expect(view.hypotheses.items[0]?.description).toContain("状态:");
    expect(view.hypotheses.items[0]?.description).toContain("置信度:");
    expect(view.hypotheses.items[0]?.description).toContain("关联实体:");
    expect(view.hypotheses.items[0]?.description).toContain("区分验证:");
    expect(view.verification.state).toBe("ready");
    expect(view.verification.items[0]?.title).toBe("query_service_metrics");
    expect(view.confidence.state).toBe("ready");
    expect(view.confidence.updates).toHaveLength(2);
    expect(view.confidence.updates[0]?.summary).toContain("Redis connection saturation");
    expect(view.remediation.state).toBe("loading");
  });

  it("nests candidate-specific evidence and confidence history under each hypothesis", () => {
    const candidateSnapshots = createCandidateSnapshots();
    const session = createSession("diagnosing");
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [
        {
          id: "tool-redis-metrics",
          kind: "tool",
          toolName: "query_service_metrics",
          params: { service: "auth-svc", window: "5m" },
          timestamp: "2026-04-08T10:04:00.000Z",
          status: "success",
          summaryLines: ["redis_timeout: +240%", "retry_rate: +180%"],
        },
      ],
      candidates: candidateSnapshots[1]?.candidates ?? [],
      candidateSnapshots,
      events: [],
      localAuditRecords: [],
    });

    expect(view.hypotheses.detailMode).toBe("expanded");
    expect(view.hypotheses.items[0]?.evidenceItems.map((item) => item.summary)).toEqual([
      "Redis timeout observed",
      "Retry amplification confirmed",
      "Check Redis saturation before scaling rollout.",
    ]);
    expect(view.hypotheses.items[1]?.evidenceItems.map((item) => item.summary)).toEqual([
      "Database queue rose",
      "Queue increase trails the Redis timeout",
      "Check whether queue depth stays high after Redis recovers.",
    ]);
    expect(view.hypotheses.items[0]?.confidenceUpdates).toHaveLength(2);
    expect(view.hypotheses.items[0]?.confidenceUpdates[1]?.summary).toContain("46%");
    expect(view.hypotheses.items[0]?.confidenceUpdates[1]?.summary).toContain("86%");
  });

  it("filters prompt-like hypothesis summaries and evidence payloads from display", () => {
    const session = createSession("diagnosing");
    session.diagnosis_result = null;

    const noisyPrompt =
      "query=Diagnose the operational issue described by the alert below using a read-only ReAct workflow. " +
      "This is AIServiceTTFT diagnosis; prioritize deterministic service->pod->node->gpu evidence chain before broad exploration. " +
      "Available read-only tools for this run: [\"gpu.get_processes\"].";

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [],
      candidates: [
        {
          id: "candidate-noise-filter",
          title: "GPU 争用",
          summary: noisyPrompt,
          confidence: 0.74,
          confidenceLabel: "74%",
          statusLabel: "当前根因",
          statusTone: "accent",
          evidenceFor: [noisyPrompt, "GPU util 持续 99%，请求排队时长同步抬升。"],
          evidenceAgainst: [noisyPrompt],
          entities: ["worker-03"],
          rank: 1,
          evidenceSummary: noisyPrompt,
          distinguishingVerification: noisyPrompt,
          isPrimary: true,
        },
      ],
      candidateSnapshots: [
        {
          id: "candidate-noise-filter-snapshot",
          timestamp: "2026-04-08T10:05:00.000Z",
          candidates: [
            {
              id: "candidate-noise-filter",
              title: "GPU 争用",
              summary: noisyPrompt,
              confidence: 0.74,
              confidenceLabel: "74%",
              statusLabel: "当前根因",
              statusTone: "accent",
              evidenceFor: [noisyPrompt, "GPU util 持续 99%，请求排队时长同步抬升。"],
              evidenceAgainst: [noisyPrompt],
              entities: ["worker-03"],
              rank: 1,
              evidenceSummary: noisyPrompt,
              distinguishingVerification: noisyPrompt,
              isPrimary: true,
            },
          ],
        },
      ],
      events: [],
      localAuditRecords: [],
    });

    expect(view.hypotheses.items[0]?.summary).toBe("");
    expect(view.hypotheses.items[0]?.evidenceItems.map((item) => item.summary)).toEqual([
      "GPU util 持续 99%，请求排队时长同步抬升。",
    ]);
  });

  it("keeps support-evidence tone consistent across primary and non-primary hypotheses", () => {
    const candidateSnapshots = createCandidateSnapshots();
    const session = createSession("diagnosing");
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [],
      candidates: candidateSnapshots[1]?.candidates ?? [],
      candidateSnapshots,
      events: [],
      localAuditRecords: [],
    });

    const supportTones = view.hypotheses.items.flatMap((item) =>
      item.evidenceItems.filter((entry) => entry.kind === "support").map((entry) => entry.tone),
    );

    expect(supportTones.length).toBeGreaterThan(0);
    expect(new Set(supportTones)).toEqual(new Set(["success"]));
  });

  it("collapses hypothesis-level validation details once the root cause is settled", () => {
    const candidateSnapshots = createCandidateSnapshots();

    const view = buildDiagnosisModifiedReportView({
      session: createSession("approval_required"),
      timeline: [],
      candidates: candidateSnapshots[1]?.candidates ?? [],
      candidateSnapshots,
      events: [],
      localAuditRecords: [],
    });

    expect(view.hypotheses.detailMode).toBe("collapsed");
    expect(view.hypotheses.description).toContain("默认折叠细节");
    expect(view.hypotheses.items[0]?.evidenceItems.length).toBeGreaterThan(0);
    expect(view.hypotheses.items[0]?.confidenceUpdates.length).toBeGreaterThan(0);
  });

  it("keeps confidence loading when only an initial candidate snapshot exists", () => {
    const candidateSnapshots = createCandidateSnapshots().slice(0, 1);
    const session = createSession("diagnosing");
    session.diagnosis_result = null;

    const view = buildDiagnosisModifiedReportView({
      session,
      timeline: [
        {
          id: "message-topology-context",
          kind: "message",
          role: "assistant",
          content:
            '[HumanMessage - topology_context]\nTopology context:\n{"roots":["redis-primary"],"affected_count":1,"affected_entities":[{"id":"service:auth-svc","name":"auth-svc"}],"summary":"topology blast radius: redis-primary impacts auth-svc"}',
          timestamp: "2026-04-08T10:01:00.000Z",
        },
      ],
      candidates: candidateSnapshots[0]?.candidates ?? [],
      candidateSnapshots,
      events: [],
      localAuditRecords: [],
    });

    expect(view.confidence.state).toBe("loading");
    expect(view.progress.activeStepId).toBe("hypotheses");
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
    expect(view.context.state).toBe("ready");
    expect(view.context.topologyEmptyReason).toBe("no_direct_relations");
    expect(view.hypotheses.state).toBe("loading");
    expect(view.hypotheses.summary).toBe("Waiting for candidate root-cause selection.");
    expect(view.hypotheses.description).toContain("confidence movement");
    expect(view.rootCause.state).toBe("loading");
    expect(view.remediation.state).toBe("loading");
    expect(view.conclusion.title).toBe("等待形成明确结论");
    expect(view.candidateChanges).toHaveLength(0);
  });

  it("builds a report-preview default state before diagnosis starts", () => {
    const view = buildDiagnosisModifiedReportView({
      session: undefined,
      timeline: [],
      candidates: [],
      events: [],
      localAuditRecords: [],
    });

    expect(view.overview.title).toBe("诊断报告");
    expect(view.overview.subtitle).toBe("诊断开始后，将在此持续生成结构化分析结论与修复建议");
    expect(view.overview.status.label).toBe("未开始");
    expect(view.overview.updatedAt).toBeUndefined();
    expect(view.rootCauseReady).toBe(false);
  });
});
