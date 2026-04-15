import { Position, type Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { TOPOLOGY_CANVAS_METRICS } from "./canvasConfig";
import {
  deriveModifiedEdgeBundles,
  deriveModifiedEdgeRouting,
  deriveModifiedSmoothStepPathOptions,
  getModifiedHandlePosition,
  MODIFIED_EDGE_HANDLE_IDS,
} from "./modifiedEdgeRouting";

const metrics = TOPOLOGY_CANVAS_METRICS.modified;

function createNode(id: string, x: number, y: number): Node {
  return {
    id,
    position: { x, y },
    data: {},
    width: metrics.nodeWidth,
    height: metrics.nodeHeight,
  };
}

describe("modified edge routing", () => {
  it("uses right-to-left handles when the target is on the right", () => {
    const routing = deriveModifiedEdgeRouting({
      sourceNode: createNode("a", 120, 160),
      targetNode: createNode("b", 420, 166),
      metrics,
    });

    expect(routing.edgeType).toBe("default");
    expect(routing.sourceHandle).toBe(MODIFIED_EDGE_HANDLE_IDS.source.right);
    expect(routing.targetHandle).toBe(MODIFIED_EDGE_HANDLE_IDS.target.left);
  });

  it("uses left-to-right handles when the target is on the left", () => {
    const routing = deriveModifiedEdgeRouting({
      sourceNode: createNode("a", 420, 166),
      targetNode: createNode("b", 120, 160),
      metrics,
    });

    expect(routing.sourceHandle).toBe(MODIFIED_EDGE_HANDLE_IDS.source.left);
    expect(routing.targetHandle).toBe(MODIFIED_EDGE_HANDLE_IDS.target.right);
  });

  it("uses bottom-to-top handles when the target is below", () => {
    const routing = deriveModifiedEdgeRouting({
      sourceNode: createNode("a", 240, 120),
      targetNode: createNode("b", 250, 420),
      metrics,
    });

    expect(routing.sourceHandle).toBe(MODIFIED_EDGE_HANDLE_IDS.source.bottom);
    expect(routing.targetHandle).toBe(MODIFIED_EDGE_HANDLE_IDS.target.top);
  });

  it("falls back to the closest-facing pair for diagonal aggregate-like layouts", () => {
    const routing = deriveModifiedEdgeRouting({
      sourceNode: createNode("aggregate", 620, 120),
      targetNode: createNode("host", 360, 320),
      metrics,
    });

    expect(routing.edgeType).toBe("default");
    expect([MODIFIED_EDGE_HANDLE_IDS.source.left, MODIFIED_EDGE_HANDLE_IDS.source.bottom]).toContain(routing.sourceHandle);
    expect([MODIFIED_EDGE_HANDLE_IDS.target.right, MODIFIED_EDGE_HANDLE_IDS.target.top]).toContain(routing.targetHandle);
  });

  it("maps handle ids back to the expected React Flow positions", () => {
    expect(getModifiedHandlePosition(MODIFIED_EDGE_HANDLE_IDS.source.left)).toBe(Position.Left);
    expect(getModifiedHandlePosition(MODIFIED_EDGE_HANDLE_IDS.source.right)).toBe(Position.Right);
    expect(getModifiedHandlePosition(MODIFIED_EDGE_HANDLE_IDS.source.top)).toBe(Position.Top);
    expect(getModifiedHandlePosition(MODIFIED_EDGE_HANDLE_IDS.source.bottom)).toBe(Position.Bottom);
  });

  it("derives grouped smoothstep options by relation family", () => {
    expect(
      deriveModifiedSmoothStepPathOptions({
        sourceType: "switch",
        targetType: "port",
        relationType: "contains",
      }),
    ).toEqual({ borderRadius: 36, offset: 20 });

    expect(
      deriveModifiedSmoothStepPathOptions({
        sourceType: "node",
        targetType: "gpu",
        relationType: "contains",
      }),
    ).toEqual({ borderRadius: 14, offset: 8 });

    expect(
      deriveModifiedSmoothStepPathOptions({
        sourceType: "node",
        targetType: "service",
        relationType: "runs_on",
      }),
    ).toEqual({ borderRadius: 22, offset: 12 });

    expect(
      deriveModifiedSmoothStepPathOptions({
        sourceType: "service",
        targetType: "pod",
        relationType: "aggregated",
        isAggregated: true,
      }),
    ).toEqual({ borderRadius: 22, offset: 12 });

    expect(
      deriveModifiedSmoothStepPathOptions({
        sourceType: "rack",
        targetType: "cluster",
        relationType: "aggregated",
        isAggregated: true,
      }),
    ).toEqual({ borderRadius: 30, offset: 18 });

    expect(
      deriveModifiedSmoothStepPathOptions({
        sourceType: "rack",
        targetType: "cluster",
        relationType: "depends_on",
      }),
    ).toEqual({ borderRadius: 26, offset: 14 });
  });

  it("marks high-density same-target edges as bundled", () => {
    const targetNode = createNode("target", 620, 260);
    const nodes = [
      createNode("s1", 180, 140),
      createNode("s2", 180, 190),
      createNode("s3", 180, 240),
      createNode("s4", 180, 290),
      targetNode,
    ];

    const bundleMeta = deriveModifiedEdgeBundles({
      edges: ["s1", "s2", "s3", "s4"].map((sourceId, index) => ({
        edgeId: `e${index + 1}`,
        sourceId,
        targetId: targetNode.id,
        sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
        targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
      })),
      nodeLookup: new Map(nodes.map((node) => [node.id, node])),
      metrics,
    });

    expect(bundleMeta.size).toBe(4);
    expect(bundleMeta.get("e1")?.bundleSize).toBe(4);
    expect(bundleMeta.get("e1")?.axis).toBe("horizontal");
    expect(bundleMeta.get("e1")?.mergeRatio).toBeCloseTo(0.64);
  });

  it("does not enable bundling when edge count is below threshold", () => {
    const targetNode = createNode("target", 620, 260);
    const nodes = [
      createNode("s1", 180, 140),
      createNode("s2", 180, 190),
      createNode("s3", 180, 240),
      targetNode,
    ];

    const bundleMeta = deriveModifiedEdgeBundles({
      edges: ["s1", "s2", "s3"].map((sourceId, index) => ({
        edgeId: `e${index + 1}`,
        sourceId,
        targetId: targetNode.id,
        sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
        targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
      })),
      nodeLookup: new Map(nodes.map((node) => [node.id, node])),
      metrics,
    });

    expect(bundleMeta.size).toBe(0);
  });

  it("does not enable bundling when orthogonal spread is too wide", () => {
    const targetNode = createNode("target", 620, 260);
    const nodes = [
      createNode("s1", 180, 60),
      createNode("s2", 180, 240),
      createNode("s3", 180, 460),
      createNode("s4", 180, 720),
      targetNode,
    ];

    const bundleMeta = deriveModifiedEdgeBundles({
      edges: ["s1", "s2", "s3", "s4"].map((sourceId, index) => ({
        edgeId: `e${index + 1}`,
        sourceId,
        targetId: targetNode.id,
        sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
        targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
      })),
      nodeLookup: new Map(nodes.map((node) => [node.id, node])),
      metrics,
    });

    expect(bundleMeta.size).toBe(0);
  });

  it("assigns stable bundle index ordering by orthogonal axis and source position", () => {
    const targetNode = createNode("target", 620, 260);
    const nodes = [
      createNode("sA", 170, 200),
      createNode("sB", 200, 200),
      createNode("sC", 180, 240),
      createNode("sD", 190, 280),
      targetNode,
    ];

    const bundleMeta = deriveModifiedEdgeBundles({
      edges: [
        {
          edgeId: "edge-b",
          sourceId: "sB",
          targetId: targetNode.id,
          sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
          targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
        },
        {
          edgeId: "edge-a",
          sourceId: "sA",
          targetId: targetNode.id,
          sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
          targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
        },
        {
          edgeId: "edge-c",
          sourceId: "sC",
          targetId: targetNode.id,
          sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
          targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
        },
        {
          edgeId: "edge-d",
          sourceId: "sD",
          targetId: targetNode.id,
          sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
          targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
        },
      ],
      nodeLookup: new Map(nodes.map((node) => [node.id, node])),
      metrics,
    });

    expect(bundleMeta.get("edge-a")?.bundleIndex).toBe(0);
    expect(bundleMeta.get("edge-b")?.bundleIndex).toBe(1);
    expect(bundleMeta.get("edge-c")?.bundleIndex).toBe(2);
    expect(bundleMeta.get("edge-d")?.bundleIndex).toBe(3);
  });
});
