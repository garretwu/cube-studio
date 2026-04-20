import { afterEach, describe, expect, it, vi } from "vitest";

import type { TopologyExplorerResponse, TopologyObject, TopologyRelation } from "../../api/types";
import { pruneTopologyExplorerResponse } from "./topologyPrune";

const now = "2026-04-16T00:00:00.000Z";

function buildNode(
  id: string,
  namespace: string | undefined,
  type: TopologyObject["type"],
  attributes: TopologyObject["attributes"] = {},
): TopologyObject {
  return {
    id,
    name: namespace ? `${namespace}/${id.split(":").at(-1)}` : id,
    type,
    status: "healthy",
    layer: type === "cluster" ? "physical" : "service",
    domain: "aidc",
    region: "cn",
    zone: "z1",
    summary: "fixture",
    tags: [],
    updatedAt: now,
    attributes: namespace ? { namespace, ...attributes } : attributes,
  };
}

function buildEdge(id: string, source: string, target: string): TopologyRelation {
  return {
    id,
    source,
    target,
    relationType: "contains",
    status: "healthy",
    isCritical: false,
    impactLevel: "low",
    label: "contains",
    isAggregated: false,
  };
}

function buildFixture(): TopologyExplorerResponse {
  return {
    site: {
      id: "site-1",
      name: "site-1",
      region: "cn",
      zone: "z1",
      domain: "aidc",
      summary: "fixture",
    },
    nodes: [
      buildNode("cluster-a", undefined, "cluster"),
      buildNode("ns:monitoring", "monitoring", "service", { kind: "namespace_group" }),
      buildNode("svc:monitoring:prometheus", "monitoring", "service"),
      buildNode("pod:monitoring:prometheus-0", "monitoring", "pod"),
      buildNode("ns:service", "service", "service", { kind: "namespace_group" }),
      buildNode("svc:service:model", "service", "service"),
      buildNode("pod:service:model-0", "service", "pod"),
    ],
    edges: [
      buildEdge("e-removed", "svc:monitoring:prometheus", "pod:monitoring:prometheus-0"),
      buildEdge("e-kept", "svc:service:model", "pod:service:model-0"),
      buildEdge("e-cross", "cluster-a", "pod:monitoring:prometheus-0"),
    ],
    paths: [
      {
        id: "path-removed",
        entryNodeId: "pod:monitoring:prometheus-0",
        rootCauseNodeId: "cluster-a",
        affectedNodeIds: ["svc:monitoring:prometheus"],
        edgeIds: ["e-removed", "e-cross"],
        impactLevel: "medium",
        status: "active",
        summary: "removed path",
      },
      {
        id: "path-kept",
        entryNodeId: "pod:service:model-0",
        rootCauseNodeId: "cluster-a",
        affectedNodeIds: ["svc:service:model", "pod:monitoring:prometheus-0"],
        edgeIds: ["e-kept", "e-cross"],
        impactLevel: "low",
        status: "active",
        summary: "kept path",
      },
    ],
    lastUpdated: now,
  };
}

describe("topology namespace pruning", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("removes excluded namespaces with their pods, services, edges, and path references", () => {
    const pruned = pruneTopologyExplorerResponse(buildFixture());

    expect(pruned.nodes.map((node) => node.id)).toEqual([
      "cluster-a",
      "ns:service",
      "svc:service:model",
      "pod:service:model-0",
    ]);
    expect(pruned.edges.map((edge) => edge.id)).toEqual(["e-kept"]);
    expect(pruned.paths.map((path) => path.id)).toEqual(["path-kept"]);
    expect(pruned.paths[0].affectedNodeIds).toEqual(["svc:service:model"]);
    expect(pruned.paths[0].edgeIds).toEqual(["e-kept"]);
  });

  it("allows the namespace exclude list to be overridden by Vite env", () => {
    vi.stubEnv("VITE_TOPOLOGY_NAMESPACE_EXCLUDELIST", "service");

    const pruned = pruneTopologyExplorerResponse(buildFixture());

    expect(pruned.nodes.some((node) => node.id === "pod:monitoring:prometheus-0")).toBe(true);
    expect(pruned.nodes.some((node) => node.id === "pod:service:model-0")).toBe(false);
  });
});
