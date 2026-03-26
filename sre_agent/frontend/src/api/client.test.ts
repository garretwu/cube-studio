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
});
