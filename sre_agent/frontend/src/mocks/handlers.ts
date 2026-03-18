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
  http.get("/api/ontology", async () => {
    await delay(120);
    return HttpResponse.json({
      nodes: topologyNodes,
      edges: topologyEdges,
      active_alerts: alerts.filter((alert) => alert.status === "firing").length,
      recent_events: [
        "Canary validation in progress for vllm-latency",
        "GPU thermal pressure detected on node-gpu-01",
      ],
    });
  }),
  http.get("/api/alerts", async () => {
    await delay(100);
    return HttpResponse.json({ alerts, clusters: alertClusters });
  }),
  http.get("/api/diagnosis/session/current", async () => {
    await delay(140);
    return HttpResponse.json(diagnosisSession);
  }),
  http.get("/api/remediation/overview", async () => {
    await delay(120);
    return HttpResponse.json(remediationOverview);
  }),
  http.post("/api/remediation/:sessionId/approve", async ({ request }) => {
    await delay(90);
    const body = (await request.json()) as { approved: boolean };
    return HttpResponse.json({ success: true, status: body.approved ? "approved" : "rejected" });
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
