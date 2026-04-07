import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiClient } from "./client";
import { server } from "../test/server";

describe("apiClient.getTopology", () => {
  it("unwraps SREResponse topology payloads", async () => {
    server.use(
      http.get("/api/topology", async () =>
        HttpResponse.json({
          success: true,
          data: {
            nodes: [
              {
                id: "node-a",
                entity_type: "node",
                name: "node-a",
                properties: { zone: "az-1" },
                status: "healthy",
                updated_at: "2026-03-25T00:00:00Z",
              },
            ],
            edges: [
              {
                source_id: "service:vllm",
                target_id: "node-a",
                relation: "depends_on",
                properties: {},
              },
            ],
            active_alerts: 0,
            recent_events: [],
          },
          error: null,
          trace_id: "trace-test-topology",
          timestamp: "2026-03-25T00:00:00Z",
        }),
      ),
    );

    const response = await apiClient.getTopology();

    expect(response.nodes[0]?.id).toBe("node-a");
    expect(response.edges[0]?.relation).toBe("depends_on");
    expect(response.active_alerts).toBe(0);
  });

  it("uses backend session contract and unwraps envelope", async () => {
    server.use(
      http.get("/api/sessions/sess-1", async () =>
        HttpResponse.json({
          success: true,
          data: {
            session_id: "sess-1",
            alert: {
              alert_name: "VLLMInterTokenLatencyP95High",
              severity: "warning",
              labels: {},
              annotations: {},
              starts_at: "2026-03-26T00:00:00Z",
              fingerprint: "fp-1",
              status: "firing",
              source: "alertmanager",
            },
            status: "running",
            duration_seconds: 1,
          },
          error: null,
          trace_id: "trace-session",
          timestamp: "2026-03-26T00:00:00Z",
        }),
      ),
    );

    const session = await apiClient.getDiagnosisSession("sess-1");
    expect(session?.session_id).toBe("sess-1");
    expect(session?.status).toBe("running");
  });

  it("returns null when no diagnosis sessions are available", async () => {
    server.use(http.get("/api/sessions", async () => HttpResponse.json([])));

    const session = await apiClient.getDiagnosisSession();
    expect(session).toBeNull();
  });

  it("uses remediate approve route", async () => {
    let called = "";
    server.use(
      http.post("/api/remediate/:sessionId/approve", async ({ params }) => {
        called = String(params.sessionId);
        return HttpResponse.json({
          success: true,
          data: {
            plan_id: "plan-1",
            success: true,
            steps_completed: 2,
            steps_total: 2,
            duration_seconds: 10,
            error: null,
          },
          error: null,
          trace_id: "trace-approve",
          timestamp: "2026-03-26T00:00:00Z",
        });
      }),
    );

    const result = await apiClient.approveRemediation("sess-approve", true, "tester");
    expect(called).toBe("sess-approve");
    expect(result.success).toBe(true);
    expect(result.steps_completed).toBe(2);
  });

  it("returns session_id for duplicate handle response from error.details", async () => {
    server.use(
      http.post("/api/handle", async () =>
        HttpResponse.json({
          success: true,
          data: null,
          error: {
            code: "ALERT_DUPLICATE",
            message: "duplicate alert, see session sess-dup-1",
            details: { session_id: "sess-dup-1" },
          },
          trace_id: "trace-handle-dup",
          timestamp: "2026-03-26T00:00:00Z",
        }),
      ),
    );

    const sessionId = await apiClient.handleAlert({
      alert_name: "VLLMInterTokenLatencyP95High",
      severity: "warning",
      labels: {},
      annotations: {},
      starts_at: "2026-03-26T00:00:00Z",
      fingerprint: "fp-dup-1",
      status: "firing",
      source: "alertmanager",
    });

    expect(sessionId).toBe("sess-dup-1");
  });

  it("loads session summaries from /api/sessions", async () => {
    server.use(
      http.get("/api/sessions", async () =>
        HttpResponse.json({
          success: true,
          data: [
            {
              session_id: "sess-2",
              status: "diagnosed",
              alert_name: "VLLMInterTokenLatencyP95High",
              severity: "warning",
              fingerprint: "fp-2",
              outcome: null,
              duration_seconds: 12,
              updated_at: "2026-03-26T00:00:00Z",
            },
          ],
          error: null,
          trace_id: "trace-sessions",
          timestamp: "2026-03-26T00:00:00Z",
        }),
      ),
    );

    const sessions = await apiClient.getSessions();
    expect(sessions).toHaveLength(1);
    expect(sessions[0]?.session_id).toBe("sess-2");
  });

  it("normalizes nested session collections from /api/sessions", async () => {
    server.use(
      http.get("/api/sessions", async () =>
        HttpResponse.json({
          success: true,
          data: {
            sessions: [
              {
                session_id: "sess-nested",
                status: "diagnosed",
                alert_name: "VLLMInterTokenLatencyP95High",
                severity: "warning",
                fingerprint: "fp-nested",
                outcome: null,
                duration_seconds: 14,
                updated_at: "2026-03-26T00:00:00Z",
              },
            ],
          },
          error: null,
          trace_id: "trace-sessions-nested",
          timestamp: "2026-03-26T00:00:00Z",
        }),
      ),
    );

    const sessions = await apiClient.getSessions();
    expect(sessions).toHaveLength(1);
    expect(sessions[0]?.session_id).toBe("sess-nested");
  });

  it("filters blocked alerts from /api/alerts response", async () => {
    server.use(
      http.get("/api/alerts", async () =>
        HttpResponse.json({
          success: true,
          data: {
            alerts: [
              {
                alert_name: "GPU utilization is high",
                severity: "warning",
                labels: { alertname: "GPU utilization is high" },
                annotations: {},
                starts_at: "2026-03-26T00:00:00Z",
                fingerprint: "fp-blocked",
                status: "firing",
              },
              {
                alert_name: "VLLMInterTokenLatencyP95High",
                severity: "critical",
                labels: { alertname: "VLLMInterTokenLatencyP95High" },
                annotations: {},
                starts_at: "2026-03-26T00:01:00Z",
                fingerprint: "fp-allowed",
                status: "firing",
              },
            ],
            clusters: [
              { cluster_id: "c-1", summary: "mixed", severity: "critical", alerts: ["fp-blocked", "fp-allowed"] },
            ],
          },
          error: null,
          trace_id: "trace-alerts",
          timestamp: "2026-03-26T00:00:00Z",
        }),
      ),
    );

    const payload = await apiClient.getAlerts();
    expect(payload.alerts).toHaveLength(1);
    expect(payload.alerts[0]?.fingerprint).toBe("fp-allowed");
    expect(payload.clusters).toHaveLength(1);
    expect(payload.clusters[0]?.alerts).toEqual(["fp-allowed"]);
  });

  it("blocks diagnose request when alert is filtered locally", async () => {
    let called = false;
    server.use(
      http.post("/api/diagnose", async () => {
        called = true;
        return HttpResponse.json({
          success: true,
          data: null,
          error: null,
          trace_id: "trace-diagnose",
          timestamp: "2026-03-26T00:00:00Z",
        });
      }),
    );

    await expect(
      apiClient.diagnoseAlert({
        alert_name: "GPUUtilizationHigh",
        severity: "warning",
        labels: { alertname: "GPUUtilizationHigh" },
        annotations: {},
        starts_at: "2026-03-26T00:00:00Z",
        fingerprint: "fp-blocked-2",
        status: "firing",
      }),
    ).rejects.toThrow("temporarily filtered");

    expect(called).toBe(false);
  });

  it("falls back to /api/diagnose when /api/diagnose/start is unavailable", async () => {
    let startCalled = false;
    let diagnoseCalled = false;
    server.use(
      http.post("/api/diagnose/start", async () => {
        startCalled = true;
        return HttpResponse.json({ message: "not found" }, { status: 404 });
      }),
      http.post("/api/diagnose", async () => {
        diagnoseCalled = true;
        return HttpResponse.json({
          success: true,
          data: {
            session_id: "sess-start-fallback",
            alert: {
              alert_name: "VLLMInterTokenLatencyP95High",
              severity: "warning",
              labels: {},
              annotations: {},
              starts_at: "2026-03-26T00:00:00Z",
              fingerprint: "fp-fallback",
              status: "firing",
              source: "alertmanager",
            },
            status: "diagnosing",
            duration_seconds: 0,
          },
          error: null,
          trace_id: "trace-diagnose-fallback",
          timestamp: "2026-03-26T00:00:00Z",
        });
      }),
    );

    const session = await apiClient.startDiagnoseAlert({
      alert_name: "VLLMInterTokenLatencyP95High",
      severity: "warning",
      labels: {},
      annotations: {},
      starts_at: "2026-03-26T00:00:00Z",
      fingerprint: "fp-fallback",
      status: "firing",
      source: "alertmanager",
    });

    expect(startCalled).toBe(true);
    expect(diagnoseCalled).toBe(true);
    expect(session.session_id).toBe("sess-start-fallback");
  });

  it("blocks handle request when alert is filtered locally", async () => {
    let called = false;
    server.use(
      http.post("/api/handle", async () => {
        called = true;
        return HttpResponse.json({
          success: true,
          data: { session_id: "sess-3" },
          error: null,
          trace_id: "trace-handle",
          timestamp: "2026-03-26T00:00:00Z",
        });
      }),
    );

    await expect(
      apiClient.handleAlert({
        alert_name: "GPU utilization is high",
        severity: "warning",
        labels: { alertname: "GPU utilization is high" },
        annotations: {},
        starts_at: "2026-03-26T00:00:00Z",
        fingerprint: "fp-blocked-3",
        status: "firing",
      }),
    ).rejects.toThrow("temporarily filtered");

    expect(called).toBe(false);
  });

  it("falls back to /api/diagnosis/sessions when /api/sessions fails", async () => {
    server.use(
      http.get("/api/sessions", async () => HttpResponse.json({ message: "boom" }, { status: 500 })),
      http.get("/api/diagnosis/sessions", async () =>
        HttpResponse.json([
          {
            session_id: "sess-fallback",
            status: "diagnosed",
            alert_name: "VLLMInterTokenLatencyP95High",
            severity: "warning",
            fingerprint: "fp-fallback",
            outcome: null,
            duration_seconds: 10,
            updated_at: "2026-03-26T00:00:00Z",
          },
        ]),
      ),
    );

    const sessions = await apiClient.getDiagnosisHistorySessions();
    expect(sessions).toHaveLength(1);
    expect(sessions[0]?.session_id).toBe("sess-fallback");
  });
  it("loads chat history from /api/chat/history", async () => {
    server.use(
      http.get("/api/chat/history", async () =>
        HttpResponse.json({
          success: true,
          data: [
            {
              id: "chat-1",
              role: "assistant",
              content: "hello",
              created_at: "2026-03-26T00:00:00Z",
            },
          ],
          error: null,
          trace_id: "trace-chat-history",
          timestamp: "2026-03-26T00:00:00Z",
        }),
      ),
    );

    const history = await apiClient.getChatHistory();
    expect(history).toHaveLength(1);
    expect(history[0]?.id).toBe("chat-1");
  });

  it("loads knowledge datasets from /api/knowledge/datasets", async () => {
    server.use(
      http.get("/api/knowledge/datasets", async () =>
        HttpResponse.json({
          success: true,
          data: [
            { id: "dataset-default", name: "SRE Dataset", document_count: 3, status: "ready" },
            { id: "dataset-network", name: "Network Dataset", document_count: 2, status: "ready" },
          ],
          error: null,
          trace_id: "trace-knowledge-datasets",
          timestamp: "2026-04-03T00:00:00Z",
        }),
      ),
    );

    const datasets = await apiClient.getKnowledgeDatasets();
    expect(datasets).toHaveLength(2);
    expect(datasets[0]?.id).toBe("dataset-default");
  });

  it("accepts raw array payload from /api/knowledge/documents", async () => {
    server.use(
      http.get("/api/knowledge/documents", async () =>
        HttpResponse.json([
          {
            id: "kb-raw-1",
            title: "Raw payload doc",
            source: "docs/raw.md",
            category: "runbook",
            excerpt: "raw payload",
            tags: [],
            score: 0.8,
          },
        ]),
      ),
    );

    const docs = await apiClient.getKnowledgeSources(undefined, 1, 20, "dataset-runbook");
    expect(docs).toHaveLength(1);
    expect(docs[0]?.id).toBe("kb-raw-1");
  });

  it("passes dataset_id when querying knowledge documents and segments", async () => {
    let docsDatasetId = "";
    let segmentsDatasetId = "";
    server.use(
      http.get("/api/knowledge/documents", async ({ request }) => {
        const url = new URL(request.url);
        docsDatasetId = url.searchParams.get("dataset_id") ?? "";
        return HttpResponse.json([]);
      }),
      http.get("/api/knowledge/segments/search", async ({ request }) => {
        const url = new URL(request.url);
        segmentsDatasetId = url.searchParams.get("dataset_id") ?? "";
        return HttpResponse.json([]);
      }),
    );

    await apiClient.getKnowledgeSources(undefined, 1, 20, "dataset-network");
    await apiClient.searchKnowledgeSegments("roce", 5, "dataset-network");

    expect(docsDatasetId).toBe("dataset-network");
    expect(segmentsDatasetId).toBe("dataset-network");
  });
});
