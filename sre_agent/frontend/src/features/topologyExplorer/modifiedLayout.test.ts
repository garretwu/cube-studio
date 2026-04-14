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
    expect(allBounds.height).toBeLessThan(1400);
    expect(serviceBounds.width).toBeGreaterThan(500);
    expect(serviceBounds.height).toBeLessThan(420);
  });
});
