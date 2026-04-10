import { topologyExplorerOnlineMock } from "../../mocks/topologyExplorerOnlineMock";
import { topologyExplorerMock } from "../../mocks/topologyExplorerData";
import {
  GLOBAL_TOPOLOGY_SERVICE_NODE_LIMIT,
  getGlobalTopologyDisplayData,
  getImpactPathIdForNode,
  getImpactTopology,
  getNeighborDepths,
  getObjectTopologyDetail,
  getPrimaryImpactPathId,
  getStageTopology,
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

  it("builds a stage topology that narrows to search hits and their direct neighbors", () => {
    const stage = getStageTopology(topologyExplorerMock, {
      layerFilter: "all",
      searchQuery: "svc-vllm-online",
    });

    expect(stage.searchResultIds).toEqual(["svc-vllm-online"]);
    expect(stage.nodes.some((node) => node.id === "svc-vllm-online")).toBe(true);
    expect(stage.nodes.some((node) => node.id === "node-h100-02")).toBe(true);
    expect(stage.nodes.some((node) => node.id === "gpu-03")).toBe(true);
    expect(stage.edges.some((edge) => edge.id === "edge-service-node")).toBe(true);
  });

  it("limits service-layer nodes in the global topology data while keeping non-service nodes intact", () => {
    const limited = getGlobalTopologyDisplayData(topologyExplorerOnlineMock)!;
    const originalServiceLayerNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.layer === "service");
    const limitedServiceLayerNodes = limited.nodes.filter((node) => node.layer === "service");
    const originalNonServiceNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.layer !== "service");
    const limitedNodeIds = new Set(limited.nodes.map((node) => node.id));
    const hiddenServiceNodeIds = new Set(
      originalServiceLayerNodes.slice(GLOBAL_TOPOLOGY_SERVICE_NODE_LIMIT).map((node) => node.id),
    );

    expect(limitedServiceLayerNodes).toHaveLength(GLOBAL_TOPOLOGY_SERVICE_NODE_LIMIT);
    expect(limited.nodes.filter((node) => node.layer !== "service")).toHaveLength(originalNonServiceNodes.length);
    expect(limited.edges.every((edge) => limitedNodeIds.has(edge.source) && limitedNodeIds.has(edge.target))).toBe(true);
    expect(limited.edges.some((edge) => hiddenServiceNodeIds.has(edge.source) || hiddenServiceNodeIds.has(edge.target))).toBe(false);
  });

  it("keeps hidden service-layer nodes out of the global stage but still allows full object detail", () => {
    const originalServiceLayerNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.layer === "service");
    const hiddenNode = originalServiceLayerNodes[GLOBAL_TOPOLOGY_SERVICE_NODE_LIMIT];

    expect(hiddenNode).toBeTruthy();

    const fullStage = getStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: "",
    });
    const hiddenNodeSearch = getStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: hiddenNode!.name,
    });
    const detail = getObjectTopologyDetail(topologyExplorerOnlineMock, hiddenNode!.id);

    expect(fullStage.nodes.filter((node) => node.layer === "service")).toHaveLength(GLOBAL_TOPOLOGY_SERVICE_NODE_LIMIT);
    expect(fullStage.nodes.some((node) => node.id === hiddenNode!.id)).toBe(false);
    expect(hiddenNodeSearch.searchResultIds).toEqual([]);
    expect(detail.notFound).toBe(false);
    expect(detail.focalNode?.id).toBe(hiddenNode!.id);
  });

  it("returns a focused single-object topology with only direct relations", () => {
    const detail = getObjectTopologyDetail(topologyExplorerMock, "svc-vllm-online");

    expect(detail.notFound).toBe(false);
    expect(detail.focalNode?.id).toBe("svc-vllm-online");
    expect(detail.nodes.map((node) => node.id).sort()).toEqual(
      ["cluster-infer-01", "gpu-03", "node-h100-02", "svc-vllm-online", "switch-leaf-a1"].sort(),
    );
    expect(detail.edges).toHaveLength(4);
    expect(detail.edges.map((edge) => edge.id).sort()).toEqual(
      ["edge-service-cluster", "edge-service-gpu", "edge-service-node", "edge-switch-service"].sort(),
    );
  });

  it("returns an explicit notFound state when the object id is unknown", () => {
    const detail = getObjectTopologyDetail(topologyExplorerMock, "missing-node");

    expect(detail.notFound).toBe(true);
    expect(detail.nodes).toHaveLength(0);
    expect(detail.edges).toHaveLength(0);
  });
});