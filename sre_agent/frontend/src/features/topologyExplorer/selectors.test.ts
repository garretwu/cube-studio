import { topologyExplorerMock } from "../../mocks/topologyExplorerData";
import {
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
});
