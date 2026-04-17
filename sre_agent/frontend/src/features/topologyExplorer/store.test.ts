import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TopologyExplorerResponse } from "../../api/types";
import { apiClient } from "../../api/client";
import { createTopologyExplorerState, useTopologyExplorerStore } from "./store";

vi.mock("../../api/client", () => ({
  apiClient: {
    getTopologyExplorer: vi.fn(),
  },
}));

const mockTopologyResponse: TopologyExplorerResponse = {
  site: {
    id: "site-1",
    name: "AIDC Site",
    region: "AIDC-CN",
    zone: "zone-a",
    domain: "aidc",
    summary: "fixture",
  },
  nodes: [
    {
      id: "ns:service",
      name: "service",
      type: "service",
      status: "healthy",
      layer: "service",
      domain: "aidc",
      region: "AIDC-CN",
      zone: "zone-a",
      summary: "namespace group",
      tags: [],
      updatedAt: "2026-04-16T00:00:00.000Z",
      attributes: { kind: "namespace_group", namespace: "service", cluster_id: "k8s:aidc-lab" },
    },
    {
      id: "svc:service:demo",
      name: "service/demo",
      type: "service",
      status: "healthy",
      layer: "service",
      domain: "aidc",
      region: "AIDC-CN",
      zone: "zone-a",
      summary: "k8s service",
      tags: [],
      updatedAt: "2026-04-16T00:00:00.000Z",
      attributes: { namespace: "service", cluster_id: "k8s:aidc-lab" },
    },
    {
      id: "pod:service:demo-0",
      name: "service/demo-0",
      type: "pod",
      status: "healthy",
      layer: "service",
      domain: "aidc",
      region: "AIDC-CN",
      zone: "zone-a",
      summary: "pod",
      tags: [],
      updatedAt: "2026-04-16T00:00:00.000Z",
      attributes: { namespace: "service" },
    },
  ],
  edges: [
    {
      id: "e1",
      source: "pod:service:demo-0",
      target: "ns:service",
      relationType: "contains",
      status: "healthy",
      isCritical: false,
      impactLevel: "low",
      label: "part_of",
      isAggregated: false,
    },
  ],
  paths: [],
  lastUpdated: "2026-04-16T00:00:00.000Z",
  sync_state: "ready",
  last_error: null,
};

describe("topology store", () => {
  beforeEach(() => {
    useTopologyExplorerStore.setState(createTopologyExplorerState());
    vi.mocked(apiClient.getTopologyExplorer).mockReset();
  });

  it("keeps raw topology payload without frontend prune dropping namespace groups", async () => {
    vi.mocked(apiClient.getTopologyExplorer).mockResolvedValue(mockTopologyResponse);

    await useTopologyExplorerStore.getState().fetchTopologyExplorer();
    const state = useTopologyExplorerStore.getState();

    expect(state.data?.nodes.some((node) => node.id === "ns:service")).toBe(true);
    expect(state.data?.nodes.some((node) => node.id === "pod:service:demo-0")).toBe(true);
  });
});

