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
<<<<<<< HEAD
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
=======
      nodes: topologyNodes,
      edges: topologyEdges,
      active_alerts: alerts.filter((alert) => alert.status === "firing").length,
      recent_events: [
        "vLLM 推理服务正在进行金丝雀验证",
        "node-gpu-01 检测到 GPU 热压升高",
      ],
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)
    });
  }),
  http.get("/api/alerts", async () => {
    await delay(100);
    return HttpResponse.json({ alerts, clusters: alertClusters });
  }),
  http.post("/api/handle", async () => {
    await delay(140);
    return HttpResponse.json({
      success: true,
      data: {
        session_id: diagnosisSession.session_id,
        outcome: "re_diagnosed",
        winning_candidate: {
          root_cause: diagnosisSession.diagnosis_result?.root_cause ?? "unknown",
          confidence: diagnosisSession.diagnosis_result?.confidence ?? 0.5,
        },
        attempts: [
          {
            candidate: {
              root_cause: diagnosisSession.diagnosis_result?.root_cause ?? "unknown",
              confidence: diagnosisSession.diagnosis_result?.confidence ?? 0.5,
            },
            remediation_result: {
              plan_id: "plan-rollback-01",
              success: false,
              steps_completed: 0,
              steps_total: 2,
              duration_seconds: 12,
              error: "approval required",
            },
            verification_passed: false,
            rolled_back: false,
            observations: {},
            duration_seconds: 12,
          },
        ],
        total_duration_seconds: diagnosisSession.duration_seconds,
        re_diagnosis_context: null,
      },
      error: null,
      trace_id: "trace-mock-handle",
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
      reply: {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        created_at: new Date().toISOString(),
        content: `收到指令：“${body.content}”。我建议先检查 GPU 进程列表，再关联最近 15 分钟的拓扑影响半径。`,
      },
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
      return categoryMatch && (!query || text.includes(query.toLowerCase()) || query.includes("rocev2"));
    });
    return HttpResponse.json({ results });
  }),
  http.get("/api/knowledge/documents", async () => {
    await delay(80);
    return HttpResponse.json({ documents: knowledgeDocuments });
  }),
  http.get("/api/memory/incidents", async () => {
    await delay(60);
    return HttpResponse.json(incidents);
  }),
  http.get("/api/memory/patterns", async () => {
    await delay(60);
    return HttpResponse.json(learnedPatterns);
  }),
  http.get("/api/memory/baseline", async () => {
    await delay(50);
    return HttpResponse.json(baseline);
  }),
  http.get("/api/skills", async () => {
    await delay(70);
    return HttpResponse.json(skills);
  }),
  http.get("/api/chat/history", async () => {
    await delay(50);
    return HttpResponse.json(initialChatMessages);
  }),
];
