import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "../api/client";
import { useTopologyStore } from "./topologyStore";

vi.mock("../api/client", () => ({
  apiClient: {
    getTopology: vi.fn(),
    triggerTopologyDiscover: vi.fn(),
  },
}));

describe("topologyStore", () => {
  beforeEach(() => {
    useTopologyStore.setState({
      nodes: [],
      edges: [],
      activeAlerts: 0,
      recentEvents: [],
      snapshotId: undefined,
      lastSyncedAt: undefined,
      syncState: "idle",
      wsState: "closed",
      error: undefined,
      requestState: "idle",
      selectedNodeId: undefined,
      isLoading: false,
      isDiscovering: false,
    });
    vi.resetAllMocks();
  });

  it("applies topology node upsert events", () => {
    useTopologyStore.getState().applyTopologyEvent({
      schema_version: "1.0",
      type: "topology",
      session_id: "topology",
      timestamp: "2026-03-30T12:00:00Z",
      data: {
        action: "node_upsert",
        node: {
          id: "node-a",
          entity_type: "node",
          name: "node-a",
          properties: {},
          status: "online",
          updated_at: "2026-03-30T12:00:00Z",
        },
      },
    });

    const state = useTopologyStore.getState();
    expect(state.nodes).toHaveLength(1);
    expect(state.nodes[0]?.id).toBe("node-a");
  });

  it("marks request failures distinctly from empty responses", async () => {
    vi.mocked(apiClient.getTopology).mockRejectedValue(new Error("network"));

    await useTopologyStore.getState().fetchTopology();

    const state = useTopologyStore.getState();
    expect(state.requestState).toBe("request_failed");
    expect(state.error).toContain("network");
  });

  it("records sync failure events from websocket stream", () => {
    useTopologyStore.getState().applyTopologyEvent({
      schema_version: "1.0",
      type: "topology",
      session_id: "topology",
      timestamp: "2026-03-30T12:00:00Z",
      data: {
        action: "sync_failed",
        error: "probe timeout",
      },
    });

    const state = useTopologyStore.getState();
    expect(state.syncState).toBe("error");
    expect(state.recentEvents.at(-1)).toContain("probe timeout");
  });
});

