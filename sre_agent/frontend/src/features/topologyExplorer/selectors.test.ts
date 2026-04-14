import { describe, expect, it } from "vitest";

import { topologyExplorerOnlineMock } from "../../mocks/topologyExplorerOnlineMock";
import {
  getGlobalTopologyDisplayData,
  getModifiedSearchResultIds,
  getModifiedStageTopology,
  getStageTopology,
  isSyntheticServiceAggregateNode,
} from "./selectors";

describe("topology modified selectors", () => {
  it("keeps switch ports as independent port objects instead of switch nodes", () => {
    const switchNode = topologyExplorerOnlineMock.nodes.find((node) => node.id === "sw-200g");
    const portNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.id.startsWith("sw-200g:"));

    expect(switchNode?.type).toBe("switch");
    expect(portNodes).toHaveLength(6);
    expect(portNodes.every((node) => node.type === "port")).toBe(true);
  });
  it("keeps BMC endpoints separate from worker server nodes", () => {
    const bmcNodes = topologyExplorerOnlineMock.nodes.filter((node) => node.id.startsWith("bmc:worker-"));
    const workerNodes = topologyExplorerOnlineMock.nodes.filter((node) => /^worker-\d+$/.test(node.id));

    expect(bmcNodes).toHaveLength(6);
    expect(workerNodes).toHaveLength(6);
    expect(bmcNodes.every((node) => node.type === "bmc")).toBe(true);
    expect(workerNodes.every((node) => node.type === "node")).toBe(true);
    expect(bmcNodes.map((node) => node.id.replace("bmc:", ""))).toEqual(workerNodes.map((node) => node.id));
  });
  it("keeps the default global topology service cap unchanged", () => {
    const scoped = getGlobalTopologyDisplayData(topologyExplorerOnlineMock);
    expect(scoped).toBeDefined();

    const serviceNodes = scoped?.nodes.filter((node) => node.layer === "service") ?? [];
    expect(serviceNodes.length).toBeLessThanOrEqual(20);

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

  it("returns real search hits and keeps them visible even when their siblings stay aggregated", () => {
    const firstServiceNode = topologyExplorerOnlineMock.nodes.find((node) => node.layer === "service");
    expect(firstServiceNode).toBeDefined();

    const searchResultIds = getModifiedSearchResultIds(
      topologyExplorerOnlineMock,
      "all",
      firstServiceNode?.name ?? "",
    );

    expect(searchResultIds).toContain(firstServiceNode!.id);

    const stage = getModifiedStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: firstServiceNode?.name ?? "",
      searchResultIds,
    });

    expect(stage.nodes.some((node) => node.id === firstServiceNode!.id)).toBe(true);
    expect(searchResultIds.every((id) => stage.searchResultIds.includes(id))).toBe(true);
  });
});
