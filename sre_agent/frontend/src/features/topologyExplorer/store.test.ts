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
    {
      id: "ns:monitoring",
      name: "monitoring",
      type: "service",
      status: "healthy",
      layer: "service",
      domain: "aidc",
      region: "AIDC-CN",
      zone: "zone-a",
      summary: "namespace group",
      tags: [],
      updatedAt: "2026-04-16T00:00:00.000Z",
      attributes: { kind: "namespace_group", namespace: "monitoring", cluster_id: "k8s:aidc-lab" },
    },
    {
      id: "pod:monitoring:prometheus-0",
      name: "monitoring/prometheus-0",
      type: "pod",
      status: "healthy",
      layer: "service",
      domain: "aidc",
      region: "AIDC-CN",
      zone: "zone-a",
      summary: "pod",
      tags: [],
      updatedAt: "2026-04-16T00:00:00.000Z",
      attributes: { namespace: "monitoring" },
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
    {
      id: "e2",
      source: "pod:monitoring:prometheus-0",
      target: "ns:monitoring",
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

  it("prunes excluded namespaces before storing data and searching", async () => {
    vi.mocked(apiClient.getTopologyExplorer).mockResolvedValue(mockTopologyResponse);

    await useTopologyExplorerStore.getState().fetchTopologyExplorer();
    useTopologyExplorerStore.getState().setSearchQuery("monitoring");
    const state = useTopologyExplorerStore.getState();

    expect(state.data?.nodes.some((node) => node.id === "ns:monitoring")).toBe(false);
    expect(state.data?.nodes.some((node) => node.id === "pod:monitoring:prometheus-0")).toBe(false);
    expect(state.data?.edges.some((edge) => edge.id === "e2")).toBe(false);
    expect(state.searchResultIds).toEqual([]);
    expect(state.searchFeedback).toBe("not_found");
  });
});
