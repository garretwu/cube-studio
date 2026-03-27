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
              alert_name: "GPUUtilizationHigh",
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
    expect(session.session_id).toBe("sess-1");
    expect(session.status).toBe("running");
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
});
