import { delay, http, HttpResponse } from "msw";

import {
  alertClusters,
  alerts,
  baseline,
  diagnosisSession,
  incidents,
  initialChatMessages,
  knowledgeDocuments,
  learnedPatterns,
  remediationOverview,
  skills,
  topologyEdges,
  topologyNodes,
} from "./data";

export const handlers = [
  http.get("/api/topology", async () => {
    await delay(120);
    return HttpResponse.json({
      success: true,
      data: {
        nodes: topologyNodes,
        edges: topologyEdges,
        active_alerts: alerts.filter((alert) => alert.status === "firing").length,
        recent_events: [
          "Canary validation in progress for vllm-latency",
          "GPU thermal pressure detected on node-gpu-01",
        ],
      },
      error: null,
      trace_id: "trace-mock-topology",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/alerts", async () => {
    await delay(100);
    return HttpResponse.json({
      success: true,
      data: {
        alerts,
        clusters: alertClusters,
      },
      error: null,
      trace_id: "trace-mock-alerts",
      timestamp: new Date().toISOString(),
    });
  }),
  http.post("/api/handle", async () => {
    await delay(140);
    return HttpResponse.json({
      success: true,
      data: remediationOverview,
      error: null,
      trace_id: "trace-mock-handle",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/sessions", async () => {
    await delay(80);
    return HttpResponse.json({
      success: true,
      data: [
        {
          session_id: diagnosisSession.session_id,
          status: diagnosisSession.status,
          alert_name: diagnosisSession.alert.alert_name,
          severity: diagnosisSession.alert.severity,
          fingerprint: diagnosisSession.alert.fingerprint,
          outcome: diagnosisSession.outcome,
          duration_seconds: diagnosisSession.duration_seconds,
          updated_at: "2026-03-18T12:02:00Z",
        },
      ],
      error: null,
      trace_id: "trace-mock-sessions",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/sessions/:sessionId", async () => {
    await delay(140);
    return HttpResponse.json({
      success: true,
      data: diagnosisSession,
      error: null,
      trace_id: "trace-mock-session",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/sessions/:sessionId/loop", async () => {
    await delay(120);
    return HttpResponse.json({
      success: true,
      data: remediationOverview,
      error: null,
      trace_id: "trace-mock-loop",
      timestamp: new Date().toISOString(),
    });
  }),
  http.post("/api/remediate/:sessionId/approve", async ({ request }) => {
    await delay(90);
    const body = (await request.json()) as { approved: boolean };
    return HttpResponse.json({
      success: true,
      data: {
        plan_id: "plan-rollback-01",
        success: body.approved,
        steps_completed: body.approved ? 2 : 0,
        steps_total: 2,
        duration_seconds: 31,
        error: body.approved ? null : "rejected by operator",
      },
      error: null,
      trace_id: "trace-mock-approve",
      timestamp: new Date().toISOString(),
    });
  }),
  http.post("/api/chat", async ({ request }) => {
    await delay(90);
    const body = (await request.json()) as { content: string };
    return HttpResponse.json({
      success: true,
      data: {
        reply: `Received: "${body.content}". Suggested next step: inspect GPU process list.`,
      },
      error: null,
      trace_id: "trace-mock-chat",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/knowledge/search", async ({ request }) => {
    await delay(120);
    const url = new URL(request.url);
    const query = (url.searchParams.get("query") ?? "").toLowerCase();
    const category = url.searchParams.get("category");
    const results = knowledgeDocuments.filter((doc) => {
      const categoryMatch = !category || doc.category === category;
      const text = `${doc.title} ${doc.excerpt} ${doc.tags.join(" ")}`.toLowerCase();
      return categoryMatch && (!query || text.includes(query));
    });
    return HttpResponse.json({
      success: true,
      data: results,
      error: null,
      trace_id: "trace-mock-knowledge-search",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/knowledge/documents", async () => {
    await delay(80);
    return HttpResponse.json({
      success: true,
      data: knowledgeDocuments,
      error: null,
      trace_id: "trace-mock-knowledge-docs",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/memory/incidents", async () => {
    await delay(60);
    return HttpResponse.json({
      success: true,
      data: incidents,
      error: null,
      trace_id: "trace-mock-incidents",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/memory/patterns", async () => {
    await delay(60);
    return HttpResponse.json({
      success: true,
      data: learnedPatterns,
      error: null,
      trace_id: "trace-mock-patterns",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/memory/baseline", async () => {
    await delay(50);
    return HttpResponse.json({
      success: true,
      data: baseline,
      error: null,
      trace_id: "trace-mock-baseline",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/skills", async () => {
    await delay(70);
    return HttpResponse.json({
      success: true,
      data: skills,
      error: null,
      trace_id: "trace-mock-skills",
      timestamp: new Date().toISOString(),
    });
  }),
  http.get("/api/chat/history", async () => {
    await delay(50);
    return HttpResponse.json({
      success: true,
      data: initialChatMessages,
      error: null,
      trace_id: "trace-mock-chat-history",
      timestamp: new Date().toISOString(),
    });
  }),
];
