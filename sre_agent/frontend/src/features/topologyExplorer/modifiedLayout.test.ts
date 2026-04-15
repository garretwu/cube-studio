import type { TopologyObject, TopologyRelation } from "../../api/types";
import { describe, expect, it } from "vitest";

import { topologyExplorerOnlineMock } from "../../mocks/topologyExplorerOnlineMock";
import { TOPOLOGY_CANVAS_METRICS } from "./canvasConfig";
import { computeModifiedHybridLayout } from "./modifiedLayout";
import { getModifiedStageTopology } from "./selectors";

function getBounds(points: Array<{ x: number; y: number }>) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);

  return {
    minX: Math.min(...xs),
    maxX: Math.max(...xs),
    minY: Math.min(...ys),
    maxY: Math.max(...ys),
    width: Math.max(...xs) - Math.min(...xs),
    height: Math.max(...ys) - Math.min(...ys),
  };
}

function median(values: number[]) {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    return (sorted[middle - 1] + sorted[middle]) / 2;
  }
  return sorted[middle];
}

function average(values: number[]) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function getHostNodeId(
  node: TopologyObject,
  edges: TopologyRelation[],
  nodeById: Map<string, TopologyObject>,
) {
  const connected = edges
    .filter((edge) => edge.source === node.id || edge.target === node.id)
    .map((edge) => (edge.source === node.id ? edge.target : edge.source))
    .map((id) => nodeById.get(id))
    .find((candidate) => candidate?.type === "node");

  return connected?.id;
}

describe("modified topology layout", () => {
  it("keeps the modified layered view spread horizontally instead of collapsing into a single vertical strip", () => {
    const stage = getModifiedStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: "",
    });

    const positions = computeModifiedHybridLayout(
      stage.nodes,
      stage.edges,
      "layered",
      TOPOLOGY_CANVAS_METRICS.modified,
    );

    const allPoints = Array.from(positions.values());
    const servicePoints = stage.nodes
      .filter((node) => node.layer === "service")
      .map((node) => positions.get(node.id))
      .filter((point): point is { x: number; y: number } => Boolean(point));

    const allBounds = getBounds(allPoints);
    const serviceBounds = getBounds(servicePoints);

    expect(allBounds.width).toBeGreaterThan(900);
    expect(allBounds.height).toBeLessThan(2400);
    expect(serviceBounds.width).toBeGreaterThan(120);
    expect(serviceBounds.height).toBeLessThan(2400);
  });

  it("follows the left-to-right chain and keeps cluster/switch and ports in a symmetric centerline", () => {
    const stage = getModifiedStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: "",
    });

    const positions = computeModifiedHybridLayout(
      stage.nodes,
      stage.edges,
      "layered",
      TOPOLOGY_CANVAS_METRICS.modified,
    );

    const groupByType = (type: TopologyObject["type"]) =>
      stage.nodes.filter((node) => node.type === type).map((node) => positions.get(node.id)).filter(Boolean) as Array<{
        x: number;
        y: number;
      }>;

    const clusters = groupByType("cluster");
    const switches = groupByType("switch");
    const ports = groupByType("port");
    const workers = groupByType("node");
    const services = groupByType("service");
    const pods = groupByType("pod");

    expect(clusters.length).toBeGreaterThan(0);
    expect(switches.length).toBeGreaterThan(0);
    expect(ports.length).toBeGreaterThan(0);
    expect(workers.length).toBeGreaterThan(0);
    expect(services.length).toBeGreaterThan(0);
    expect(pods.length).toBeGreaterThan(0);

    const clusterX = median(clusters.map((point) => point.x));
    const switchX = median(switches.map((point) => point.x));
    const portX = median(ports.map((point) => point.x));
    const workerX = median(workers.map((point) => point.x));
    const serviceX = median(services.map((point) => point.x));
    const podX = median(pods.map((point) => point.x));

    expect(clusterX).toBeLessThan(switchX);
    expect(switchX).toBeLessThan(portX);
    expect(portX).toBeLessThan(workerX);
    expect(workerX).toBeLessThan(serviceX);
    expect(serviceX).toBeLessThan(podX);

    const clusterY = median(clusters.map((point) => point.y));
    const switchY = median(switches.map((point) => point.y));
    const portMeanY = average(ports.map((point) => point.y));
    const upperPortCount = ports.filter((point) => point.y < switchY).length;
    const lowerPortCount = ports.filter((point) => point.y > switchY).length;

    expect(Math.abs(clusterY - switchY)).toBeLessThanOrEqual(90);
    expect(Math.abs(portMeanY - switchY)).toBeLessThanOrEqual(24);
    expect(Math.abs(upperPortCount - lowerPortCount)).toBeLessThanOrEqual(1);
  });

  it("places GPU/BMC as right-side branches around their host worker rows", () => {
    const stage = getModifiedStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: "",
    });

    const positions = computeModifiedHybridLayout(
      stage.nodes,
      stage.edges,
      "layered",
      TOPOLOGY_CANVAS_METRICS.modified,
    );

    const nodeById = new Map(stage.nodes.map((node) => [node.id, node]));

    const bmcs = stage.nodes.filter((node) => node.type === "bmc");
    const gpus = stage.nodes.filter((node) => node.type === "gpu");

    const bmcChecks = bmcs
      .map((bmc) => {
        const hostId = getHostNodeId(bmc, stage.edges, nodeById);
        if (!hostId) {
          return undefined;
        }
        const hostPos = positions.get(hostId);
        const bmcPos = positions.get(bmc.id);
        if (!hostPos || !bmcPos) {
          return undefined;
        }
        return {
          rightOfHost: bmcPos.x > hostPos.x,
          aboveHostBand: bmcPos.y < hostPos.y,
        };
      })
      .filter((value): value is { rightOfHost: boolean; aboveHostBand: boolean } => Boolean(value));

    const gpuChecks = gpus
      .map((gpu) => {
        const hostId = getHostNodeId(gpu, stage.edges, nodeById);
        if (!hostId) {
          return undefined;
        }
        const hostPos = positions.get(hostId);
        const gpuPos = positions.get(gpu.id);
        if (!hostPos || !gpuPos) {
          return undefined;
        }
        return {
          rightOfHost: gpuPos.x > hostPos.x,
          belowHostBand: gpuPos.y > hostPos.y,
        };
      })
      .filter((value): value is { rightOfHost: boolean; belowHostBand: boolean } => Boolean(value));

    expect(bmcChecks.length).toBeGreaterThan(0);
    expect(gpuChecks.length).toBeGreaterThan(0);
    expect(bmcChecks.every((check) => check.rightOfHost)).toBe(true);
    expect(bmcChecks.filter((check) => check.aboveHostBand).length).toBeGreaterThanOrEqual(
      Math.floor(bmcChecks.length * 0.7),
    );
    expect(gpuChecks.every((check) => check.rightOfHost)).toBe(true);
    expect(gpuChecks.filter((check) => check.belowHostBand).length).toBeGreaterThanOrEqual(
      Math.floor(gpuChecks.length * 0.7),
    );
  });
  it("ensures initial layered node circles do not overlap", () => {
    const stage = getModifiedStageTopology(topologyExplorerOnlineMock, {
      layerFilter: "all",
      searchQuery: "",
    });

    const positions = computeModifiedHybridLayout(
      stage.nodes,
      stage.edges,
      "layered",
      TOPOLOGY_CANVAS_METRICS.modified,
    );

    const baseRadius = TOPOLOGY_CANVAS_METRICS.modified.nodeCircleSize / 2 + 3;
    const getCollisionRadius = (node: TopologyObject) => {
      const aggregateCount = Number(node.attributes.aggregateCount ?? 0);
      if (Number.isFinite(aggregateCount) && aggregateCount > 0) {
        return baseRadius + Math.min(14, aggregateCount) * 0.35 + 4;
      }
      if (node.type === "cluster") {
        return baseRadius + 4;
      }
      if (node.type === "service" || node.type === "pod") {
        return baseRadius + 1;
      }
      return baseRadius;
    };

    const placed = stage.nodes
      .map((node) => {
        const position = positions.get(node.id);
        if (!position) {
          return undefined;
        }
        return {
          id: node.id,
          x: position.x,
          y: position.y,
          radius: getCollisionRadius(node),
        };
      })
      .filter(
        (
          item,
        ): item is { id: string; x: number; y: number; radius: number } =>
          Boolean(item),
      );

    for (let i = 0; i < placed.length; i += 1) {
      for (let j = i + 1; j < placed.length; j += 1) {
        const left = placed[i];
        const right = placed[j];
        const distance = Math.hypot(left.x - right.x, left.y - right.y);
        expect(distance).toBeGreaterThanOrEqual(left.radius + right.radius);
      }
    }
  });
});
