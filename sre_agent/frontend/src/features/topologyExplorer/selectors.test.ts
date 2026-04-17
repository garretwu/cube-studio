import { describe, expect, it } from "vitest";

import type { TopologyExplorerResponse } from "../../api/types";
import { topologyExplorerOnlineMock } from "../../mocks/topologyExplorerOnlineMock";
import {
  getGlobalTopologyDisplayData,
  getObjectTopologyDetail,
  getModifiedSearchResultIds,
  getModifiedStageTopology,
  getStageTopology,
  isSyntheticGpuAggregateNode,
  isSyntheticServiceAggregateNode,
} from "./selectors";
import { pruneTopologyExplorerResponse } from "./topologyPrune";


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
    attributes: { kind: "namespace_group" },
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

function buildServiceHostFixture(): TopologyExplorerResponse {
  const now = "2026-04-17T00:00:00.000Z";

  return {
    site: {
      id: "site-1",
      name: "Fixture Site",
      region: "cn",
      zone: "z1",
      domain: "aidc",
      summary: "fixture",
    },
    nodes: [
      {
        id: "cluster:aidc-lab",
        name: "aidc-lab",
        type: "cluster",
        status: "healthy",
        layer: "physical",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        summary: "cluster",
        tags: [],
        updatedAt: now,
        attributes: {},
      },
      {
        id: "node:worker-1",
        name: "worker-1",
        type: "node",
        status: "healthy",
        layer: "compute",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "node",
        tags: [],
        updatedAt: now,
        attributes: { cluster_id: "k8s:aidc-lab" },
      },
      {
        id: "node:worker-2",
        name: "worker-2",
        type: "node",
        status: "healthy",
        layer: "compute",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "node",
        tags: [],
        updatedAt: now,
        attributes: { cluster_id: "k8s:aidc-lab" },
      },
      {
        id: "ns:service",
        name: "service",
        type: "service",
        status: "healthy",
        layer: "service",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "namespace group",
        tags: [],
        updatedAt: now,
        attributes: { kind: "namespace_group", namespace: "service", cluster_id: "k8s:aidc-lab" },
      },
      {
        id: "svc:service:demo-a",
        name: "service/demo-a",
        type: "service",
        status: "healthy",
        layer: "service",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "service",
        tags: [],
        updatedAt: now,
        attributes: { namespace: "service", cluster_id: "k8s:aidc-lab" },
      },
      {
        id: "svc:service:demo-b",
        name: "service/demo-b",
        type: "service",
        status: "healthy",
        layer: "service",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "service",
        tags: [],
        updatedAt: now,
        attributes: { namespace: "service", cluster_id: "k8s:aidc-lab" },
      },
    ],
    edges: [
      {
        id: "e-ns-svc-a",
        source: "svc:service:demo-a",
        target: "ns:service",
        relationType: "contains",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
        label: "part_of",
      },
      {
        id: "e-ns-svc-b",
        source: "svc:service:demo-b",
        target: "ns:service",
        relationType: "contains",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
        label: "part_of",
      },
      {
        id: "e-host-a",
        source: "svc:service:demo-a",
        target: "node:worker-1",
        relationType: "runs_on",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
        label: "hosted_on",
      },
      {
        id: "e-host-b",
        source: "svc:service:demo-b",
        target: "node:worker-1",
        relationType: "runs_on",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
        label: "hosted_on",
      },
      {
        id: "e-host-c",
        source: "svc:service:demo-b",
        target: "node:worker-2",
        relationType: "runs_on",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
        label: "hosted_on",
      },
    ],
    paths: [],
    lastUpdated: now,
  };
}

describe("topology modified selectors", () => {
  it("keeps switch ports as independent port objects instead of switch nodes", () => {
    const switchNode = topologyExplorerOnlineMock.nodes.find((node) => node.id === "sw-200g");
    const portNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.id.startsWith("sw-200g:"));

    expect(switchNode?.type).toBe("switch");
    expect(portNodes).toHaveLength(3);
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
    expect(aggregateNode?.id).toMatch(/^aggregate:(ns:|unassigned)/);

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

  it("does not generate modified namespace aggregates for pruned namespaces", () => {
    const pruned = pruneTopologyExplorerResponse(topologyExplorerOnlineMock);
    const stage = getModifiedStageTopology(pruned, {
      layerFilter: "all",
      searchQuery: "",
    });
    const aggregateNamespaces = stage.nodes
      .filter((node) => isSyntheticServiceAggregateNode(node))
      .map((node) => String(node.id).replace(/^aggregate:ns:/, "").replace(/:pod$/, ""))
      .sort((left, right) => left.localeCompare(right));

    expect(aggregateNamespaces).toEqual([
      "ai-models",
      "ceph-csi",
      "cert-manager",
      "dify",
      "infra",
      "jupyter",
      "nvidia-dcgm",
      "service",
      "storage-system",
    ]);
    expect(getModifiedSearchResultIds(pruned, "all", "monitoring")).toEqual([]);
  });

  it("aggregates pods by namespace and keeps unassigned pods isolated", () => {
    const now = "2026-04-16T00:00:00.000Z";
    const fixture: TopologyExplorerResponse = {
      site: {
        id: "site-1",
        name: "site-1",
        region: "cn",
        zone: "z1",
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
          region: "cn",
          zone: "z1",
          summary: "namespace group",
          tags: [],
          updatedAt: now,
          attributes: { kind: "namespace_group", namespace: "service" },
        },
        {
          id: "ns:infra",
          name: "infra",
          type: "service",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "namespace group",
          tags: [],
          updatedAt: now,
          attributes: { kind: "namespace_group", namespace: "infra" },
        },
        {
          id: "pod:service:demo-0",
          name: "service/demo-0",
          type: "pod",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "pod",
          tags: [],
          updatedAt: now,
          attributes: { namespace: "service" },
        },
        {
          id: "pod:service:demo-1",
          name: "service/demo-1",
          type: "pod",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "pod",
          tags: [],
          updatedAt: now,
          attributes: { namespace: "service" },
        },
        {
          id: "pod:infra:demo-0",
          name: "infra/demo-0",
          type: "pod",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "pod",
          tags: [],
          updatedAt: now,
          attributes: { namespace: "infra" },
        },
        {
          id: "pod:infra:demo-1",
          name: "infra/demo-1",
          type: "pod",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "pod",
          tags: [],
          updatedAt: now,
          attributes: { namespace: "infra" },
        },
        {
          id: "pod:orphan:0",
          name: "orphan/0",
          type: "pod",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "pod",
          tags: [],
          updatedAt: now,
          attributes: {},
        },
        {
          id: "pod:orphan:1",
          name: "orphan/1",
          type: "pod",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "pod",
          tags: [],
          updatedAt: now,
          attributes: {},
        },
      ],
      edges: [
        {
          id: "e1",
          source: "ns:service",
          target: "pod:service:demo-0",
          relationType: "contains",
          status: "healthy",
          isCritical: false,
          impactLevel: "low",
          label: "part_of",
        },
        {
          id: "e2",
          source: "ns:service",
          target: "pod:service:demo-1",
          relationType: "contains",
          status: "healthy",
          isCritical: false,
          impactLevel: "low",
          label: "part_of",
        },
        {
          id: "e3",
          source: "ns:infra",
          target: "pod:infra:demo-0",
          relationType: "contains",
          status: "healthy",
          isCritical: false,
          impactLevel: "low",
          label: "part_of",
        },
        {
          id: "e4",
          source: "ns:infra",
          target: "pod:infra:demo-1",
          relationType: "contains",
          status: "healthy",
          isCritical: false,
          impactLevel: "low",
          label: "part_of",
        },
      ],
      paths: [],
      lastUpdated: now,
    };

    const stage = getModifiedStageTopology(fixture, {
      layerFilter: "all",
      searchQuery: "",
    });

    const aggregateIds = stage.nodes
      .filter((node) => isSyntheticServiceAggregateNode(node))
      .map((node) => node.id);
    expect(aggregateIds).toContain("aggregate:ns:service:pod");
    expect(aggregateIds).toContain("aggregate:ns:infra:pod");
    expect(aggregateIds).toContain("aggregate:unassigned:pod");
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

  it("hides svc:* nodes in modified main stage but keeps ns:* and pod nodes", () => {
    const now = "2026-04-16T00:00:00.000Z";
    const fixture: TopologyExplorerResponse = {
      site: {
        id: "site-1",
        name: "site-1",
        region: "cn",
        zone: "z1",
        domain: "aidc",
        summary: "fixture",
      },
      nodes: [
        {
          id: "cluster:aidc-lab",
          name: "aidc-lab",
          type: "cluster",
          status: "healthy",
          layer: "physical",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "cluster",
          tags: [],
          updatedAt: now,
          attributes: {},
        },
        {
          id: "ns:service",
          name: "service",
          type: "service",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "namespace group",
          tags: [],
          updatedAt: now,
          attributes: { kind: "namespace_group", namespace: "service", cluster_id: "k8s:aidc-lab" },
        },
        {
          id: "svc:service:demo",
          name: "service/demo",
          type: "service",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "k8s service",
          tags: [],
          updatedAt: now,
          attributes: { namespace: "service", cluster_id: "k8s:aidc-lab" },
        },
        {
          id: "pod:service:demo-0",
          name: "service/demo-0",
          type: "pod",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "cn",
          zone: "z1",
          summary: "pod",
          tags: [],
          updatedAt: now,
          attributes: { namespace: "service" },
        },
      ],
      edges: [
        {
          id: "e1",
          source: "pod:service:demo-0",
          target: "svc:service:demo",
          relationType: "depends_on",
          status: "healthy",
          isCritical: false,
          impactLevel: "low",
          label: "serves",
        },
        {
          id: "e2",
          source: "pod:service:demo-0",
          target: "ns:service",
          relationType: "contains",
          status: "healthy",
          isCritical: false,
          impactLevel: "low",
          label: "part_of",
        },
      ],
      paths: [],
      lastUpdated: now,
    };

    const stage = getModifiedStageTopology(fixture, {
      layerFilter: "all",
      searchQuery: "",
    });

    expect(stage.nodes.some((node) => node.id.startsWith("svc:"))).toBe(false);
    expect(stage.nodes.some((node) => node.id === "ns:service")).toBe(true);
    expect(stage.nodes.some((node) => node.id.startsWith("pod:service:"))).toBe(true);
  });

  it("hides svc:* nodes in default stage and prevents service search from returning hidden svc ids", () => {
    const stage = getStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: "external-model-service",
    });

    expect(stage.nodes.some((node) => node.id.startsWith("svc:"))).toBe(false);
    expect(stage.searchResultIds.some((id) => id.startsWith("svc:"))).toBe(false);
  });

  it("synthesizes visible namespace-group to node host edges on the default stage", () => {
    const fixture = buildServiceHostFixture();

    const stage = getStageTopology(fixture, {
      layerFilter: "all",
      searchQuery: "",
    });

    const hostEdges = stage.edges.filter(
      (edge) => edge.source === "ns:service" && edge.relationType === "runs_on",
    );

    expect(hostEdges).toHaveLength(2);
    expect(hostEdges.find((edge) => edge.target === "node:worker-1")?.label).toBe("运行于(2)");
    expect(hostEdges.find((edge) => edge.target === "node:worker-2")?.label).toBe("运行于");
    expect(stage.nodes.some((node) => node.id.startsWith("svc:"))).toBe(false);
  });

  it("does not synthesize namespace-group host edges when no hidden service-host edges exist", () => {
    const fixture = buildServiceHostFixture();
    fixture.edges = fixture.edges.filter((edge) => edge.relationType !== "runs_on");

    const stage = getStageTopology(fixture, {
      layerFilter: "all",
      searchQuery: "",
    });

    expect(
      stage.edges.some((edge) => edge.source === "ns:service" && edge.target.startsWith("node:")),
    ).toBe(false);
  });

  it("shows node and namespace-group as neighbors in object topology detail through synthesized host edges", () => {
    const fixture = buildServiceHostFixture();

    const serviceDetail = getObjectTopologyDetail(fixture, "ns:service");
    expect(serviceDetail.downstream.map((node) => node.id)).toEqual(
      expect.arrayContaining(["node:worker-1", "node:worker-2"]),
    );
    expect(serviceDetail.edges.some((edge) => edge.source === "ns:service" && edge.target === "node:worker-1")).toBe(true);

    const nodeDetail = getObjectTopologyDetail(fixture, "node:worker-1");
    expect(nodeDetail.upstream.map((node) => node.id)).toContain("ns:service");
    expect(nodeDetail.neighbors.map((node) => node.id)).toContain("ns:service");
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
