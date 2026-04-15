import { describe, expect, it } from "vitest";

import type { TopologyExplorerResponse } from "../../api/types";
import { topologyExplorerOnlineMock } from "../../mocks/topologyExplorerOnlineMock";
import {
  getGlobalTopologyDisplayData,
  getModifiedSearchResultIds,
  getModifiedStageTopology,
  getStageTopology,
  isSyntheticGpuAggregateNode,
  isSyntheticServiceAggregateNode,
} from "./selectors";


function buildAggregateLabelFixture(edges: TopologyExplorerResponse["edges"]): TopologyExplorerResponse {
  const now = "2026-04-15T00:00:00.000Z";
  const baseNode: Omit<TopologyExplorerResponse["nodes"][number], "id" | "name" | "type" | "layer"> = {
    status: "healthy",
    domain: "factory-a",
    region: "cn",
    zone: "sh",
    summary: "fixture",
    tags: [],
    updatedAt: now,
    attributes: {},
  };

  return {
    site: {
      id: "site-1",
      name: "Fixture Site",
      region: "cn",
      zone: "sh",
      domain: "factory-a",
      summary: "fixture site",
    },
    nodes: [
      {
        id: "cluster-a",
        name: "Cluster A",
        type: "cluster",
        layer: "physical",
        ...baseNode,
      },
      {
        id: "rack-z",
        name: "Rack Z",
        type: "rack",
        layer: "physical",
        ...baseNode,
      },
      {
        id: "svc-1",
        name: "Service 1",
        type: "service",
        layer: "service",
        ...baseNode,
      },
      {
        id: "svc-2",
        name: "Service 2",
        type: "service",
        layer: "service",
        ...baseNode,
      },
    ],
    edges,
    paths: [],
    lastUpdated: now,
  };
}
describe("topology modified selectors", () => {
  it("keeps switch ports as independent port objects instead of switch nodes", () => {
    const switchNode = topologyExplorerOnlineMock.nodes.find((node) => node.id === "sw-200g");
    const portNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.id.startsWith("sw-200g:"));

    expect(switchNode?.type).toBe("switch");
    expect(portNodes).toHaveLength(6);
    expect(portNodes.every((node) => node.type === "port")).toBe(true);
  });

  it("connects the cluster to the switch for ingress context", () => {
    const clusterNode = topologyExplorerOnlineMock.nodes.find((node) => node.type === "cluster");
    const switchNode = topologyExplorerOnlineMock.nodes.find((node) => node.type === "switch");
    expect(clusterNode).toBeDefined();
    expect(switchNode).toBeDefined();

    const hasEdge = topologyExplorerOnlineMock.edges.some((edge) => {
      const isPair =
        (edge.source === clusterNode!.id && edge.target === switchNode!.id) ||
        (edge.source === switchNode!.id && edge.target === clusterNode!.id);
      return isPair && edge.relationType === "connects_to";
    });
    expect(hasEdge).toBe(true);
  });
  it("keeps BMC endpoints separate from worker server nodes", () => {
    const bmcNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.id.startsWith("bmc:worker-"));
    const canonicalNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.id.startsWith("wj-lab-"));

    expect(bmcNodes).toHaveLength(6);
    expect(canonicalNodes).toHaveLength(7);
    expect(bmcNodes.every((node) => node.type === "bmc")).toBe(true);
    expect(canonicalNodes.every((node) => node.type === "node")).toBe(true);
  });
  it("keeps the default global topology service cap unchanged", () => {
    const scoped = getGlobalTopologyDisplayData(topologyExplorerOnlineMock);
    expect(scoped).toBeDefined();

    const serviceLayerNodes = scoped?.nodes.filter((node) => node.layer === "service") ?? [];
    expect(serviceLayerNodes.length).toBeLessThanOrEqual(20);

    const defaultStage = getStageTopology(scoped, {
      layerFilter: "all",
      searchQuery: "",
    });

    expect(defaultStage.nodes.length).toBe(scoped?.nodes.length ?? 0);
  });

  it("aggregates service-layer groups on the modified stage and expands them on demand", () => {
    const stage = getModifiedStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: "",
    });

    const aggregateNode = stage.nodes.find((node) => isSyntheticServiceAggregateNode(node));
    expect(aggregateNode).toBeDefined();

    const aggregateMemberIds = Array.isArray(aggregateNode?.attributes.aggregateMemberIds)
      ? (aggregateNode?.attributes.aggregateMemberIds as string[])
      : [];

    expect(aggregateMemberIds.length).toBeGreaterThan(0);

    const expandedStage = getModifiedStageTopology(
      topologyExplorerOnlineMock,
      {
        layerFilter: "all",
        searchQuery: "",
      },
      {
        expandedAggregateIds: [aggregateNode!.id],
      },
    );

    expect(expandedStage.nodes.some((node) => node.id === aggregateNode!.id)).toBe(false);
    expect(expandedStage.nodes.some((node) => aggregateMemberIds.includes(node.id))).toBe(true);
  });

  it("aggregates GPU nodes per host node on the modified stage", () => {
    const stage = getModifiedStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: "",
    });

    const gpuAggregate = stage.nodes.find((node) => isSyntheticGpuAggregateNode(node));
    expect(gpuAggregate).toBeDefined();
    expect(gpuAggregate?.type).toBe("gpu");
    expect(gpuAggregate?.layer).toBe("compute");

    const memberIds = Array.isArray(gpuAggregate?.attributes.aggregateMemberIds)
      ? (gpuAggregate?.attributes.aggregateMemberIds as string[])
      : [];
    expect(memberIds.length).toBeGreaterThan(0);

    // At least one healthy member GPU should be hidden by default.
    expect(stage.nodes.some((node) => memberIds.includes(node.id))).toBe(false);
  });

  it("returns real search hits and keeps them visible even when their siblings stay aggregated", () => {
    const firstPodNode = topologyExplorerOnlineMock.nodes.find((node) => String(node.attributes.rawType ?? "").toLowerCase() === "pod");
    expect(firstPodNode).toBeDefined();

    const searchResultIds = getModifiedSearchResultIds(
      topologyExplorerOnlineMock,
      "all",
      firstPodNode?.name ?? "",
    );

    expect(searchResultIds).toContain(firstPodNode!.id);

    const stage = getModifiedStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: firstPodNode?.name ?? "",
      searchResultIds,
    });

    expect(stage.nodes.some((node) => node.id === firstPodNode!.id)).toBe(true);
    expect(searchResultIds.every((id) => stage.searchResultIds.includes(id))).toBe(true);
  });
  it("formats aggregated edge labels as relation(count) when merged relations are homogeneous", () => {
    const fixture = buildAggregateLabelFixture([
      {
        id: "e-1",
        source: "cluster-a",
        target: "svc-1",
        relationType: "depends_on",
        label: "serve to",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
      },
      {
        id: "e-2",
        source: "svc-1",
        target: "rack-z",
        relationType: "depends_on",
        label: "serve to",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
      },
      {
        id: "e-3",
        source: "cluster-a",
        target: "svc-2",
        relationType: "depends_on",
        label: "serve to",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
      },
      {
        id: "e-4",
        source: "svc-2",
        target: "rack-z",
        relationType: "depends_on",
        label: "serve to",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
      },
    ]);

    const stage = getStageTopology(fixture, {
      layerFilter: "physical",
      searchQuery: "",
    });

    const edge = stage.edges.find(
      (item) => item.source === "cluster-a" && item.target === "rack-z" && item.isAggregated,
    );

    expect(edge?.label).toBe("serve to(2)");
  });

  it("formats aggregated edge labels as N relations when merged relations are mixed", () => {
    const fixture = buildAggregateLabelFixture([
      {
        id: "e-1",
        source: "cluster-a",
        target: "svc-1",
        relationType: "depends_on",
        label: "serve to",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
      },
      {
        id: "e-2",
        source: "svc-1",
        target: "rack-z",
        relationType: "depends_on",
        label: "serve to",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
      },
      {
        id: "e-3",
        source: "cluster-a",
        target: "svc-2",
        relationType: "connects_to",
        label: "depends on",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
      },
      {
        id: "e-4",
        source: "svc-2",
        target: "rack-z",
        relationType: "connects_to",
        label: "depends on",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
      },
    ]);

    const stage = getStageTopology(fixture, {
      layerFilter: "physical",
      searchQuery: "",
    });

    const edge = stage.edges.find(
      (item) => item.source === "cluster-a" && item.target === "rack-z" && item.isAggregated,
    );

    expect(edge?.label).toBe("2 relations");
  });
});
