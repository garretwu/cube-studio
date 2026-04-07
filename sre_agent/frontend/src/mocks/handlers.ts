import { delay, http, HttpResponse } from "msw";

import {
  alertClusters,
  alerts,
  diagnosisHistorySessions,
  diagnosisSession,
  initialChatMessages,
  knowledgeBaseDetails,
  knowledgeBases,
  knowledgeDatasets,
  knowledgeDocuments,
  skills,
  topologyEdges,
  topologyExplorerMock,
  topologyNodes,
} from "./data";

function buildSession(sessionId: string) {
  if (sessionId === diagnosisSession.session_id) {
    return diagnosisSession;
  }

  const matched = diagnosisHistorySessions.find((item) => item.session_id === sessionId);
  if (!matched) {
    return null;
  }

  return {
    ...diagnosisSession,
    session_id: matched.session_id,
    status: matched.status,
    duration_seconds: matched.duration_seconds,
    outcome: matched.outcome ?? null,
    alert: {
      ...diagnosisSession.alert,
      alert_name: matched.alert_name,
      severity: matched.severity,
      fingerprint: matched.session_id,
    },
  };
}

const topologyPayload = {
  nodes: topologyNodes,
  edges: topologyEdges,
  active_alerts: alerts.filter((alert) => alert.status === "firing").length,
  recent_events: ["Topology sync completed", "node-gpu-01 reported high GPU temperature"],
  snapshot_id: "snapshot-mock-001",
  last_synced_at: "2026-03-30T12:00:00Z",
  sync_state: "ready" as const,
};

const topologyStatusPayload = {
  snapshot_id: "snapshot-mock-001",
  sync_state: "ready" as const,
  mode: "live",
  last_synced_at: "2026-03-30T12:00:00Z",
  last_started_at: "2026-03-30T11:59:57Z",
  last_error: null,
  scanner_counts: {
    k8s: { nodes: 10, edges: 9 },
    prometheus: { nodes: 3, edges: 2 },
  },
};

const loopPayload = {
  session_id: diagnosisSession.session_id,
  outcome: "re_diagnosed" as const,
  winning_candidate: {
    root_cause: diagnosisSession.diagnosis_result?.root_cause ?? "pending",
    confidence: diagnosisSession.diagnosis_result?.confidence ?? 0.5,
  },
  attempts: [],
  total_duration_seconds: diagnosisSession.duration_seconds,
  re_diagnosis_context: null,
};

export const handlers = [
  http.get("/api/topology", async () => {
    await delay(80);
    return HttpResponse.json(topologyPayload);
  }),
  http.get("/api/ontology", async () => {
    await delay(80);
    return HttpResponse.json(topologyPayload);
  }),
  http.get("/api/topology/status", async () => {
    await delay(60);
    return HttpResponse.json(topologyStatusPayload);
  }),
  http.post("/api/topology/discover", async () => {
    await delay(60);
    return HttpResponse.json(topologyStatusPayload);
  }),
  http.get("/api/topology-explorer", async () => {
    await delay(80);
    return HttpResponse.json(topologyExplorerMock);
  }),

  http.get("/api/alerts", async () => {
    await delay(60);
    return HttpResponse.json({ alerts, clusters: alertClusters });
  }),
  http.post("/api/handle", async () => {
    await delay(60);
    return HttpResponse.json({
      success: true,
      data: {
        session_id: diagnosisSession.session_id,
      },
      error: null,
      trace_id: "trace-handle-mock",
      timestamp: new Date().toISOString(),
    });
  }),

  http.get("/api/sessions", async ({ request }) => {
    await delay(60);
    const url = new URL(request.url);
    const limitValue = Number(url.searchParams.get("limit") ?? Number.POSITIVE_INFINITY);
    const summaries = diagnosisHistorySessions.slice(0, Number.isFinite(limitValue) ? limitValue : undefined).map((item) => ({
      session_id: item.session_id,
      status: item.status,
      alert_name: item.alert_name,
      severity: item.severity,
      fingerprint: item.session_id,
      outcome: item.outcome ?? null,
      duration_seconds: item.duration_seconds,
      updated_at: item.updated_at,
    }));

    return HttpResponse.json(summaries);
  }),
  http.get("/api/sessions/:sessionId", async ({ params }) => {
    await delay(60);
    const sessionId = String(params.sessionId ?? "");
    const session = buildSession(sessionId);
    if (!session) {
      return HttpResponse.json({ message: "session not found" }, { status: 404 });
    }
    return HttpResponse.json(session);
  }),
  http.get("/api/sessions/:sessionId/loop", async ({ params }) => {
    await delay(60);
    const sessionId = String(params.sessionId ?? "");
    return HttpResponse.json({
      ...loopPayload,
      session_id: sessionId || diagnosisSession.session_id,
    });
  }),
  http.get("/api/sessions/:sessionId/events", async () => {
    await delay(40);
    return HttpResponse.json([]);
  }),

  http.get("/api/diagnosis/session/current", async () => {
    await delay(80);
    return HttpResponse.json(diagnosisSession);
  }),
  http.get("/api/diagnosis/sessions", async () => {
    await delay(80);
    return HttpResponse.json(diagnosisHistorySessions);
  }),

  http.get("/api/chat/history", async () => {
    await delay(50);
    return HttpResponse.json(initialChatMessages);
  }),
  http.post("/api/chat", async ({ request }) => {
    await delay(50);
    const body = (await request.json()) as { content?: string };
    return HttpResponse.json({
      reply: {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        created_at: new Date().toISOString(),
        content: `宸叉敹鍒帮細${body.content ?? ""}`,
      },
    });
  }),

  http.get("/api/knowledge/bases", async () => {
    await delay(70);
    return HttpResponse.json({ items: knowledgeBases });
  }),
  http.get("/api/knowledge/bases/:knowledgeBaseId", async ({ params }) => {
    await delay(70);
    const knowledgeBaseId = String(params.knowledgeBaseId ?? "");
    const matched = knowledgeBaseDetails.find((item) => item.id === knowledgeBaseId);
    if (!matched) {
      return HttpResponse.json({ message: "knowledge base not found" }, { status: 404 });
    }
    return HttpResponse.json(matched);
  }),
  http.get("/api/knowledge/search", async ({ request }) => {
    await delay(70);
    const url = new URL(request.url);
    const query = (url.searchParams.get("query") ?? "").toLowerCase();
    const category = url.searchParams.get("category");
    const results = knowledgeDocuments.filter((doc) => {
      const categoryMatch = !category || doc.category === category;
      const text = `${doc.title} ${doc.excerpt} ${doc.tags.join(" ")}`.toLowerCase();
      return categoryMatch && (!query || text.includes(query));
    });
    return HttpResponse.json({ results });
  }),
  http.get("/api/knowledge/datasets", async ({ request }) => {
    await delay(60);
    const url = new URL(request.url);
    const keyword = (url.searchParams.get("keyword") ?? "").trim().toLowerCase();
    const rows = keyword
      ? knowledgeDatasets.filter((item) => `${item.name} ${item.description ?? ""}`.toLowerCase().includes(keyword))
      : knowledgeDatasets;
    return HttpResponse.json(rows);
  }),
  http.get("/api/knowledge/dataset", async ({ request }) => {
    await delay(60);
    const url = new URL(request.url);
    const datasetId = (url.searchParams.get("dataset_id") ?? "").trim();
    const matched = knowledgeDatasets.find((item) => item.id === datasetId) ?? knowledgeDatasets[0];
    return HttpResponse.json(matched);
  }),
  http.get("/api/knowledge/documents", async ({ request }) => {
    await delay(70);
    const url = new URL(request.url);
    const keyword = (url.searchParams.get("keyword") ?? "").trim().toLowerCase();
    const datasetId = (url.searchParams.get("dataset_id") ?? "").trim();
    const scoped =
      datasetId === "dataset-network" ? knowledgeDocuments.filter((item) => item.category === "hardware") : knowledgeDocuments;
    const rows = keyword
      ? scoped.filter((item) => `${item.title} ${item.excerpt} ${item.tags.join(" ")}`.toLowerCase().includes(keyword))
      : scoped;
    return HttpResponse.json(rows);
  }),
  http.get("/api/knowledge/documents/:documentId", async ({ params }) => {
    await delay(60);
    const documentId = String(params.documentId ?? "");
    const detail = knowledgeDocuments.find((item) => item.id === documentId) ?? knowledgeDocuments[0];
    return HttpResponse.json(detail);
  }),
  http.get("/api/knowledge/documents/:documentId/segments", async ({ params, request }) => {
    await delay(70);
    const documentId = String(params.documentId ?? "");
    const url = new URL(request.url);
    const keyword = (url.searchParams.get("keyword") ?? "").trim().toLowerCase();
    const baseSegments = [
      {
        id: `${documentId}-seg-1`,
        document_id: documentId,
        content: "Check ECN and PFC counters before changing routing weights.",
        status: "enabled",
        score: 0.93,
      },
      {
        id: `${documentId}-seg-2`,
        document_id: documentId,
        content: "Use 10% canary traffic and monitor p95 latency for 120 seconds.",
        status: "enabled",
        score: 0.87,
      },
    ];
    const rows = keyword ? baseSegments.filter((item) => item.content.toLowerCase().includes(keyword)) : baseSegments;
    return HttpResponse.json(rows);
  }),
  http.get("/api/knowledge/segments/search", async ({ request }) => {
    await delay(70);
    const url = new URL(request.url);
    const query = (url.searchParams.get("query") ?? "").trim().toLowerCase();
    const datasetId = (url.searchParams.get("dataset_id") ?? "").trim();
    const rows = [
      {
        id: "seg-search-1",
        document_id: datasetId === "dataset-network" ? "kb-1" : "kb-2",
        content: "ECN misconfiguration can amplify RoCE tail latency.",
        status: "enabled",
        score: 0.9,
      },
      {
        id: "seg-search-2",
        document_id: "kb-2",
        content: "Kill abnormal gpu-burn process then verify queue depth.",
        status: "enabled",
        score: 0.85,
      },
    ].filter((item) => !query || item.content.toLowerCase().includes(query));
    return HttpResponse.json(rows);
  }),

  http.get("/api/skills/:skillId", async ({ params }) => {
    await delay(50);
    const skillId = String(params.skillId ?? "");
    const matched = skills.find((skill) => skill.id === skillId);
    if (!matched) {
      return HttpResponse.json({ message: "skill not found" }, { status: 404 });
    }
    return HttpResponse.json(matched);
  }),
  http.get("/api/skills", async () => {
    await delay(50);
    return HttpResponse.json(skills);
  }),
  http.get("/api/tools/channels/status", async () => {
    await delay(50);
    return HttpResponse.json({
      runtime_mode: "degraded",
      channels: [
        {
          name: "ssh",
          health: "ready",
          required_by_tools: ["shell_exec"],
          enabled: true,
          mode: "degraded",
          last_error: null,
          last_checked_at: "2026-03-30T12:00:00Z",
        },
      ],
    });
  }),

  http.post("/api/remediate/:sessionId/approve", async () => {
    await delay(60);
    return HttpResponse.json({
      plan_id: "plan-1",
      success: true,
      steps_completed: 2,
      steps_total: 2,
      duration_seconds: 10,
      error: null,
    });
  }),
  http.post("/api/remediate/:sessionId/plan/revise", async ({ params, request }) => {
    await delay(60);
    const body = (await request.json()) as { instruction?: string };
    const sessionId = String(params.sessionId ?? diagnosisSession.session_id);
    return HttpResponse.json({
      session_id: sessionId,
      plan_version: 2,
      plan: {
        plan_id: "plan-2",
        root_cause: diagnosisSession.diagnosis_result?.root_cause ?? "unknown",
        description: body.instruction ?? "revised",
        steps: [],
        estimated_impact: "low",
        confidence: 0.7,
        priority: "P2",
      },
      session: {
        ...diagnosisSession,
        session_id: sessionId,
        status: "approval_required",
      },
    });
  }),
  http.post("/api/remediation/:sessionId/approve", async () => {
    await delay(60);
    return HttpResponse.json({
      success: true,
      status: "approved",
    });
  }),
];

