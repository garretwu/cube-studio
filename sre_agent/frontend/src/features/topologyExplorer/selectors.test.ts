import { topologyExplorerMock } from "../../mocks/topologyExplorerData";
import {
  buildTopologyTree,
  getImpactPathIdForNode,
  getImpactTopology,
  getNeighborDepths,
  getPrimaryImpactPathId,
  getVisibleTopology,
  searchTopologyObjects,
} from "./selectors";

describe("topology explorer selectors", () => {
  it("searches topology objects by fuzzy text", () => {
    const results = searchTopologyObjects(topologyExplorerMock.nodes, "gpu-03");
    expect(results.map((item) => item.id)).toContain("gpu-03");
  });

  it("creates aggregated edges when layer filtering hides intermediate nodes", () => {
    const visible = getVisibleTopology(topologyExplorerMock, {
      statusFilter: "all",
      layerFilter: "compute",
      summaryFilter: "all",
    });

    expect(visible.nodes.some((node) => node.id === "cluster-infer-01")).toBe(true);
    expect(
      visible.edges.some(
        (edge) => edge.isAggregated && edge.source === "cluster-infer-01" && edge.target === "node-h100-01",
      ),
    ).toBe(true);
  });

  it("resolves the primary impact path and path ownership for root cause and impacted objects", () => {
    const primaryPathId = getPrimaryImpactPathId(topologyExplorerMock);

    expect(primaryPathId).toBe("path-vllm-gpu03");
    expect(getImpactPathIdForNode(topologyExplorerMock.paths, "gpu-03")).toBe(primaryPathId);
    expect(getImpactPathIdForNode(topologyExplorerMock.paths, "svc-vllm-online")).toBe(primaryPathId);
  });

  it("returns impact topology by path id and neighbor depths for an impacted service", () => {
    const impact = getImpactTopology(topologyExplorerMock, "path-vllm-gpu03");
    const depths = getNeighborDepths(topologyExplorerMock.edges, "svc-vllm-online");

    expect(impact?.path.id).toBe("path-vllm-gpu03");
    expect(impact?.rootCauseNode?.id).toBe("gpu-03");
    expect(impact?.affectedNodes.some((node) => node.id === "node-h100-02")).toBe(true);
    expect(impact?.edges).toHaveLength(3);
    expect(depths.get("gpu-03")).toBe(1);
  });

  it("keeps non-default namespace workloads in the tree and search results", () => {
    const response = {
      site: {
        id: "aidc-site",
        name: "AIDC Site",
        region: "AIDC-CN",
        zone: "zone-a",
        domain: "aidc",
        summary: "test",
      },
      nodes: [
        {
          id: "k8s:lab-cluster",
          name: "lab-cluster",
          type: "cluster",
          status: "healthy",
          layer: "physical",
          domain: "aidc",
          region: "AIDC-CN",
          zone: "zone-a",
          summary: "cluster",
          tags: [],
          updatedAt: "2026-04-08T00:00:00Z",
          attributes: {},
        },
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
          summary: "team-a demo service",
          tags: ["team-a"],
          updatedAt: "2026-04-08T00:00:00Z",
          attributes: { namespace: "team-a" },
        },
        {
          id: "pod:team-a:demo-0",
          name: "team-a/demo-0",
          type: "pod",
          status: "healthy",
          layer: "service",
          domain: "aidc",
          region: "AIDC-CN",
          zone: "zone-a",
          cluster: "k8s:lab-cluster",
          summary: "team-a demo pod",
          tags: ["team-a"],
          updatedAt: "2026-04-08T00:00:00Z",
          attributes: { namespace: "team-a" },
        },
      ],
      edges: [],
      paths: [],
      lastUpdated: "2026-04-08T00:00:00Z",
    } as const;

    const tree = buildTopologyTree(response);
    const searchResults = searchTopologyObjects(response.nodes, "team-a");

    expect(tree?.children?.[1]?.children?.[0]?.children?.map((node) => node.label)).toEqual([
      "team-a/demo",
      "team-a/demo-0",
    ]);
    expect(searchResults.map((node) => node.id)).toEqual(["svc:team-a:demo", "pod:team-a:demo-0"]);
  });
});
