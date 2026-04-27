import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiClient, streamDiagnosis } from "./client";
import { server } from "../test/server";

const streamAlert = {
  alert_name: "LatencyHigh",
  severity: "warning" as const,
  labels: { service: "auth-svc" },
  annotations: {},
  starts_at: "2026-03-26T00:00:00Z",
  fingerprint: "fp-stream",
  status: "firing" as const,
};

describe("streamDiagnosis", () => {
  it("passes through token semantic fields from SSE events", async () => {
    server.use(
      http.post("/api/diagnose/stream", async () =>
        HttpResponse.text(
          [
            "event: token_delta",
            'data: {"type":"token_delta","session_id":"sess-stream","data":{"content":"诊断结论","stream_channel":"content","phase":"final"}}',
            "",
            "event: done",
            'data: {"type":"done","session_id":"sess-stream","data":{}}',
            "",
          ].join("\n"),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );

    const events: Array<{ type: string; session_id?: string; data?: Record<string, unknown> }> = [];
    await streamDiagnosis(streamAlert, [], (event) => events.push(event));

    expect(events[0]).toMatchObject({
      type: "token_delta",
      session_id: "sess-stream",
      data: {
        content: "诊断结论",
        stream_channel: "content",
        phase: "final",
      },
    });
    expect(events[1]).toMatchObject({ type: "done", session_id: "sess-stream" });
  });
});

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

  it("keeps multi-namespace pod and service entities visible when falling back from /api/topology-explorer", async () => {
    server.use(
      http.get("/api/topology-explorer", async () => HttpResponse.json({ message: "not found" }, { status: 404 })),
      http.get("/api/topology", async () =>
        HttpResponse.json({
          success: true,
          data: {
            nodes: [
              {
                id: "pod:default:demo",
                entity_type: "k8s_pod",
                name: "demo",
                properties: { namespace: "default", cluster_id: "k8s:lab-cluster" },
                status: "Running",
                updated_at: "2026-03-25T00:00:00Z",
              },
              {
                id: "svc:default:demo",
                entity_type: "inference_service",
                name: "demo",
                properties: { namespace: "default", cluster_id: "k8s:lab-cluster" },
                status: "online",
                updated_at: "2026-03-25T00:00:00Z",
              },
              {
                id: "pod:team-a:demo",
                entity_type: "k8s_pod",
                name: "demo",
                properties: { namespace: "team-a", cluster_id: "k8s:lab-cluster" },
                status: "Running",
                updated_at: "2026-03-25T00:00:00Z",
              },
              {
                id: "svc:team-a:demo",
                entity_type: "inference_service",
                name: "demo",
                properties: { namespace: "team-a", cluster_id: "k8s:lab-cluster" },
                status: "online",
                updated_at: "2026-03-25T00:00:00Z",
              },
            ],
            edges: [
              {
                source_id: "pod:default:demo",
                target_id: "node-a",
                relation: "hosted_on",
                properties: {},
              },
              {
                source_id: "svc:default:demo",
                target_id: "pod:default:demo",
                relation: "serves",
                properties: {},
              },
              {
                source_id: "pod:team-a:demo",
                target_id: "node-b",
                relation: "hosted_on",
                properties: {},
              },
              {
                source_id: "svc:team-a:demo",
                target_id: "pod:team-a:demo",
                relation: "serves",
                properties: {},
              },
            ],
            active_alerts: 0,
            recent_events: [],
            snapshot_id: "snapshot-1",
            last_synced_at: "2026-03-25T00:00:00Z",
            sync_state: "ready",
          },
          error: null,
          trace_id: "trace-topology-explorer-fallback",
          timestamp: "2026-03-25T00:00:00Z",
        }),
      ),
    );

    const response = await apiClient.getTopologyExplorer();
    expect(response.nodes.find((node) => node.id === "pod:default:demo")?.type).toBe("pod");
    expect(response.nodes.find((node) => node.id === "svc:default:demo")?.type).toBe("service");
    expect(response.nodes.find((node) => node.id === "pod:team-a:demo")?.name).toBe("team-a/demo");
    expect(response.nodes.find((node) => node.id === "svc:team-a:demo")?.name).toBe("team-a/demo");
  });

  it("unwraps direct /api/topology-explorer responses", async () => {
    server.use(
      http.get("/api/topology-explorer", async () =>
        HttpResponse.json({
          success: true,
          data: {
            site: {
              id: "aidc-site",
              name: "AIDC Site",
              region: "AIDC-CN",
              zone: "zone-a",
              domain: "aidc",
              summary: "Topology explorer snapshot mapped from /api/topology",
            },
            nodes: [
              {
                id: "svc:team-a:demo",
                name: "team-a/demo",
                type: "service",
                status: "healthy",
                layer: "service",
                domain: "aidc",
                region: "AIDC-CN",
                zone: "zone-a",
                cluster: "k8s:lab-cluster",
                summary: "team-a/demo (inference_service) namespace=team-a cluster=k8s:lab-cluster",
                tags: ["team-a", "k8s", "k8s:lab-cluster"],
                updatedAt: "2026-04-08T00:00:00Z",
                attributes: { namespace: "team-a", cluster_id: "k8s:lab-cluster" },
              },
            ],
            edges: [
              {
                id: "edge-0-svc:team-a:demo-pod:team-a:demo-0",
                source: "svc:team-a:demo",
                target: "pod:team-a:demo-0",
                relationType: "depends_on",
                status: "healthy",
                isCritical: false,
                impactLevel: "low",
                label: "serves",
                isAggregated: false,
              },
            ],
            paths: [],
            lastUpdated: "2026-04-08T00:00:00Z",
          },
          error: null,
          trace_id: "trace-topology-explorer-direct",
          timestamp: "2026-04-08T00:00:00Z",
        }),
      ),
    );

    const response = await apiClient.getTopologyExplorer();

    expect(response.site.id).toBe("aidc-site");
    expect(response.nodes[0]?.name).toBe("team-a/demo");
    expect(response.edges[0]?.relationType).toBe("depends_on");
    expect(response.paths).toEqual([]);
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
    window.localStorage.removeItem("sre_session_id");
    server.use(http.get("/api/sessions", async () => HttpResponse.json([])));

    const session = await apiClient.getDiagnosisSession();
    expect(session).toBeNull();
  });

  it("returns latest session when explicit session id is stale", async () => {
    window.localStorage.removeItem("sre_session_id");

    server.use(
      http.get("/api/sessions/sess-legacy", async () => HttpResponse.json({ message: "not found" }, { status: 404 })),
      http.get("/api/sessions", async () =>
        HttpResponse.json([
          {
            session_id: "sess-current",
            status: "diagnosing",
            alert_name: "Current",
            severity: "warning",
            fingerprint: "fp-current",
            outcome: null,
            duration_seconds: 1,
            updated_at: "2026-03-26T00:00:02Z",
          },
        ]),
      ),
      http.get("/api/sessions/sess-current", async () =>
        HttpResponse.json({
          session_id: "sess-current",
          alert: {
            alert_name: "Current",
            severity: "warning",
            labels: {},
            annotations: {},
            starts_at: "2026-03-26T00:00:00Z",
            fingerprint: "fp-current",
            status: "firing",
            source: "alertmanager",
          },
          status: "running",
          duration_seconds: 1,
        }),
      ),
    );

    const resolved = await apiClient.resolveActiveSession("sess-legacy");
    expect(resolved.session?.session_id).toBe("sess-current");
    expect(resolved.state).toBe("stale_redirected");
    expect(resolved.source).toBe("latest");
  });

  it("skips stale session summaries when detail lookups return 404", async () => {
    window.localStorage.removeItem("sre_session_id");

    server.use(
      http.get("/api/sessions", async () =>
        HttpResponse.json([
          {
            session_id: "sess-stale",
            status: "diagnosed",
            alert_name: "Stale",
            severity: "warning",
            fingerprint: "fp-stale",
            outcome: null,
            duration_seconds: 1,
            updated_at: "2026-03-26T00:00:00Z",
          },
          {
            session_id: "sess-good",
            status: "running",
            alert_name: "Good",
            severity: "warning",
            fingerprint: "fp-good",
            outcome: null,
            duration_seconds: 1,
            updated_at: "2026-03-26T00:00:01Z",
          },
        ]),
      ),
      http.get("/api/sessions/sess-stale", async () => HttpResponse.json({ message: "not found" }, { status: 404 })),
      http.get("/api/sessions/sess-good", async () =>
        HttpResponse.json({
          session_id: "sess-good",
          alert: {
            alert_name: "Good",
            severity: "warning",
            labels: {},
            annotations: {},
            starts_at: "2026-03-26T00:00:00Z",
            fingerprint: "fp-good",
            status: "firing",
            source: "alertmanager",
          },
          status: "running",
          duration_seconds: 2,
        }),
      ),
    );

    const session = await apiClient.getDiagnosisSession();
    expect(session?.session_id).toBe("sess-good");
  });

  it("resolves empty when explicit stale session has no fallback candidates", async () => {
    window.localStorage.removeItem("sre_session_id");
    server.use(
      http.get("/api/sessions/sess-stale-only", async () => HttpResponse.json({ message: "not found" }, { status: 404 })),
      http.get("/api/sessions", async () => HttpResponse.json([])),
    );

    const resolved = await apiClient.resolveActiveSession("sess-stale-only");
    expect(resolved.session).toBeNull();
    expect(resolved.state).toBe("empty");
    expect(resolved.source).toBe("none");
    expect(window.localStorage.getItem("sre_session_id")).toBeNull();
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

  it("uses extended timeout for remediation approval requests", async () => {
    server.use(
      http.post("/api/remediate/:sessionId/approve", async () => {
        await delay(11000);
        return HttpResponse.json({
          success: true,
          data: {
            plan_id: "plan-approve-timeout",
            success: true,
            steps_completed: 1,
            steps_total: 1,
            duration_seconds: 11,
            error: null,
          },
          error: null,
          trace_id: "trace-approve-timeout",
          timestamp: "2026-03-26T00:00:11Z",
        });
      }),
    );

    const result = await apiClient.approveRemediation("sess-approve-timeout", true, "tester");
    expect(result.success).toBe(true);
    expect(result.duration_seconds).toBe(11);
  }, 15000);

  it("keeps non-approval API timeout policy unchanged", async () => {
    server.use(
      http.get("/api/sessions/:sessionId/events", async () => {
        await delay(11000);
        return HttpResponse.json({
          success: true,
          data: [],
          error: null,
          trace_id: "trace-events-timeout-policy",
          timestamp: "2026-03-26T00:00:11Z",
        });
      }),
    );

    const events = await apiClient.getSessionEvents("sess-timeout-policy");
    expect(events).toEqual([]);
  }, 15000);

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
                alert_name: "TargetDown",
                severity: "warning",
                labels: { alertname: "TargetDown" },
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

  it("blocks diagnose request for default demo blocked alert before sending the request", async () => {
    let called = false;
    server.use(
      http.post("/api/diagnose", async () => {
        called = true;
        return HttpResponse.json({
          success: true,
          data: {
            session_id: "sess-gpu-diagnose",
            alert: {
              alert_name: "GPUUtilizationHigh",
              severity: "warning",
              labels: { alertname: "GPUUtilizationHigh" },
              annotations: {},
              starts_at: "2026-03-26T00:00:00Z",
              fingerprint: "fp-blocked-2",
              status: "firing",
              source: "alertmanager",
            },
            status: "diagnosing",
            duration_seconds: 0,
          },
          error: null,
          trace_id: "trace-diagnose",
          timestamp: "2026-03-26T00:00:00Z",
        });
      }),
    );

    await expect(
      apiClient.diagnoseAlert({
        alert_name: "TargetDown",
        severity: "warning",
        labels: { alertname: "TargetDown" },
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

  it("blocks startDiagnoseAlert for default demo blocked alert before sending the request", async () => {
    let called = false;
    server.use(
      http.post("/api/diagnose/start", async () => {
        called = true;
        return HttpResponse.json({});
      }),
    );

    await expect(
      apiClient.startDiagnoseAlert({
        alert_name: "DeadMansSwitch",
        severity: "warning",
        labels: { alertname: "DeadMansSwitch" },
        annotations: {},
        starts_at: "2026-03-26T00:00:00Z",
        fingerprint: "fp-blocked-start",
        status: "firing",
      }),
    ).rejects.toThrow("temporarily filtered");
    expect(called).toBe(false);
  });

  it("blocks handle request for default demo blocked alert before sending the request", async () => {
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
        alert_name: "PrometheusOperatorDown",
        severity: "warning",
        labels: { alertname: "PrometheusOperatorDown" },
        annotations: {},
        starts_at: "2026-03-26T00:00:00Z",
        fingerprint: "fp-blocked-3",
        status: "firing",
      }),
    ).rejects.toThrow("temporarily filtered");
    expect(called).toBe(false);
  });

  it("throws when /api/sessions fails while loading diagnosis history", async () => {
    server.use(
      http.get("/api/sessions", async () => HttpResponse.json({ message: "boom" }, { status: 500 })),
    );

    await expect(apiClient.getDiagnosisHistorySessions()).rejects.toThrow("Request failed with status 500.");
  });

  it("derives canary and full rollout progress from remediation timeline events", async () => {
    server.use(
      http.get("/api/sessions/sess-rollout", async () =>
        HttpResponse.json({
          session_id: "sess-rollout",
          alert: {
            alert_name: "VLLMInterTokenLatencyP95High",
            severity: "critical",
            labels: {},
            annotations: {},
            starts_at: "2026-03-26T00:00:00Z",
            fingerprint: "fp-rollout",
            status: "firing",
            source: "alertmanager",
          },
          status: "remediating",
          duration_seconds: 120,
          diagnosis_result: {
            root_cause: "GPU contention",
            root_cause_layer: "service",
            root_cause_entities: ["svc-vllm"],
            confidence: 0.93,
            hypotheses: [],
            impact_summary: "latency spike",
            affected_services: ["vllm"],
            triage_priority: "P1",
            diagnosis_certainty: "confirmed",
            recommended_fix: {
              plan_id: "plan-rollout-v1",
              root_cause: "GPU contention",
              description: "Drain canary traffic before rolling out globally.",
              steps: [
                {
                  step_id: 1,
                  description: "Drain canary slice",
                  tool: "traffic_shift",
                  params: { percentage: 0.1 },
                  rollback_tool: "traffic_restore",
                  verification: { method: "wait", wait_seconds: 30 },
                  timeout: 60,
                },
                {
                  step_id: 2,
                  description: "Observe metrics",
                  tool: "metrics_query",
                  params: { query: "vllm_p95_ms" },
                  rollback_tool: null,
                  verification: { method: "promql", query: "vllm_p95_ms < 300" },
                  timeout: 120,
                },
              ],
              canary: {
                enabled: true,
                target_percentage: 0.1,
                monitor_duration: 120,
                success_criteria: [{ metric: "vllm_p95_ms", operator: "<", value: 300 }],
              },
              estimated_impact: "low",
              confidence: 0.93,
              priority: "P1",
            },
          },
        }),
      ),
      http.get("/api/sessions/sess-rollout/events", async () =>
        HttpResponse.json([
          {
            schema_version: "1.0",
            type: "remediation_progress",
            session_id: "sess-rollout",
            timestamp: "2026-03-26T00:00:01Z",
            data: { stage: "canary_started", progress: 10 },
          },
          {
            schema_version: "1.0",
            type: "remediation_progress",
            session_id: "sess-rollout",
            timestamp: "2026-03-26T00:00:02Z",
            data: { stage: "canary_succeeded", progress: 100 },
          },
          {
            schema_version: "1.0",
            type: "remediation_progress",
            session_id: "sess-rollout",
            timestamp: "2026-03-26T00:00:03Z",
            data: { stage: "full_rollout_started", progress: 35 },
          },
          {
            schema_version: "1.0",
            type: "remediation_progress",
            session_id: "sess-rollout",
            timestamp: "2026-03-26T00:00:04Z",
            data: { stage: "full_rollout_progress", progress: 72 },
          },
        ]),
      ),
    );

    const overview = await apiClient.getRemediationOverview("sess-rollout");

    expect(overview.progress.batch_status).toEqual([
      { batch: "金丝雀", progress: 100, status: "canary_succeeded" },
      { batch: "全量", progress: 72, status: "full_rollout_progress" },
    ]);
  });

  it("prefers execution_succeeded event over stale remediating session status", async () => {
    server.use(
      http.get("/api/sessions/sess-remediation-success", async () =>
        HttpResponse.json({
          session_id: "sess-remediation-success",
          alert: {
            alert_name: "AIServiceTTFTP99High",
            severity: "warning",
            labels: {},
            annotations: {},
            starts_at: "2026-04-27T00:00:00Z",
            fingerprint: "fp-remediation-success",
            status: "firing",
            source: "alertmanager",
          },
          status: "remediating",
          diagnosis_result: {
            confidence: 0.93,
            impact_summary: "latency spike",
            affected_services: ["vllm"],
            triage_priority: "P1",
            diagnosis_certainty: "confirmed",
            recommended_fix: {
              plan_id: "plan-success",
              root_cause: "External load",
              description: "Terminate load process.",
              steps: [
                {
                  step_id: 1,
                  description: "Terminate process",
                  tool: "kill_process",
                  params: { node: "worker-03", pid: 123 },
                  verification: { method: "wait", wait_seconds: 1 },
                  timeout: 30,
                },
                {
                  step_id: 2,
                  description: "Observe metrics",
                  tool: "prometheus.query_instant",
                  params: { query: "ttft" },
                  verification: { method: "wait", wait_seconds: 1 },
                  timeout: 30,
                },
              ],
              estimated_impact: "low",
              confidence: 0.93,
              priority: "P1",
            },
          },
        }),
      ),
      http.get("/api/sessions/sess-remediation-success/events", async () =>
        HttpResponse.json([
          {
            schema_version: "1.0",
            type: "remediation_progress",
            session_id: "sess-remediation-success",
            timestamp: "2026-04-27T00:00:01Z",
            data: { stage: "execution_started" },
          },
          {
            schema_version: "1.0",
            type: "remediation_progress",
            session_id: "sess-remediation-success",
            timestamp: "2026-04-27T00:00:02Z",
            data: { stage: "execution_succeeded", steps_completed: 1 },
          },
        ]),
      ),
    );

    const overview = await apiClient.getRemediationOverview("sess-remediation-success");

    expect(overview.progress.status).toBe("resolved");
    expect(overview.progress.completed_steps).toBe(2);
    expect(overview.progress.total_steps).toBe(2);
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
