import type { TopologyObject, TopologyRelation } from "../../api/types";
import { describe, expect, it } from "vitest";

import { TOPOLOGY_CANVAS_METRICS } from "./canvasConfig";
import { computeObjectFocusLayout } from "./modifiedLayout";

function createObjectNode(id: string, name: string, type: TopologyObject["type"] = "node"): TopologyObject {
  return {
    id,
    name,
    type,
    status: "healthy",
    layer: type === "service" || type === "pod" ? "service" : type === "switch" || type === "port" ? "network" : "compute",
    domain: "test",
    region: "test",
    zone: "test",
    summary: name,
    tags: [],
    updatedAt: "2026-04-17T00:00:00.000Z",
    attributes: {},
  };
}

function createObjectEdge(
  id: string,
  source: string,
  target: string,
  relationType: TopologyRelation["relationType"],
): TopologyRelation {
  return {
    id,
    source,
    target,
    relationType,
    status: "healthy",
    isCritical: false,
    impactLevel: "low",
  };
}

describe("object focus topology layout", () => {
  it("places an object detail focal node between upstream and downstream neighbors", () => {
    const nodes = [
      createObjectNode("source", "source", "switch"),
      createObjectNode("focal", "focal", "port"),
      createObjectNode("target", "target", "node"),
    ];
    const positions = computeObjectFocusLayout(
      nodes,
      [
        createObjectEdge("e1", "source", "focal", "connects_to"),
        createObjectEdge("e2", "focal", "target", "connects_to"),
      ],
      "focal",
      TOPOLOGY_CANVAS_METRICS.modified,
    );

    expect(positions.get("source")!.x).toBeLessThan(positions.get("focal")!.x);
    expect(positions.get("target")!.x).toBeGreaterThan(positions.get("focal")!.x);
    expect(positions.get("source")!.y).toBe(positions.get("focal")!.y);
    expect(positions.get("target")!.y).toBe(positions.get("focal")!.y);
  });

  it("places contains relationships above and below the object focal node", () => {
    const nodes = [
      createObjectNode("parent", "parent", "node"),
      createObjectNode("focal", "focal", "gpu"),
      createObjectNode("child", "child", "pod"),
    ];
    const positions = computeObjectFocusLayout(
      nodes,
      [
        createObjectEdge("e1", "parent", "focal", "contains"),
        createObjectEdge("e2", "focal", "child", "contains"),
      ],
      "focal",
      TOPOLOGY_CANVAS_METRICS.modified,
    );

    expect(positions.get("parent")!.y).toBeLessThan(positions.get("focal")!.y);
    expect(positions.get("child")!.y).toBeGreaterThan(positions.get("focal")!.y);
    expect(positions.get("parent")!.x).toBe(positions.get("focal")!.x);
    expect(positions.get("child")!.x).toBe(positions.get("focal")!.x);
  });

  it("stacks multiple object neighbors without overlapping the focal node", () => {
    const nodes = [
      createObjectNode("focal", "focal"),
      createObjectNode("a", "a"),
      createObjectNode("b", "b"),
      createObjectNode("c", "c"),
    ];
    const positions = computeObjectFocusLayout(
      nodes,
      [
        createObjectEdge("e1", "a", "focal", "depends_on"),
        createObjectEdge("e2", "b", "focal", "depends_on"),
        createObjectEdge("e3", "focal", "c", "depends_on"),
      ],
      "focal",
      TOPOLOGY_CANVAS_METRICS.modified,
    );

    expect(positions.get("a")!.x).toBe(positions.get("b")!.x);
    expect(positions.get("a")!.y).not.toBe(positions.get("b")!.y);
    expect(positions.get("c")!.x).toBeGreaterThan(positions.get("focal")!.x);
  });

  it("keeps an isolated object focal node at the object detail center", () => {
    const positions = computeObjectFocusLayout(
      [createObjectNode("focal", "focal")],
      [],
      "focal",
      TOPOLOGY_CANVAS_METRICS.modified,
    );

    expect(positions.get("focal")).toEqual({ x: 525, y: 207 });
  });
});
