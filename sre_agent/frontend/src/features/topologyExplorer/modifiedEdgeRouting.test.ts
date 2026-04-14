import { Position, type Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { TOPOLOGY_CANVAS_METRICS } from "./canvasConfig";
import {
  deriveModifiedEdgeRouting,
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

    expect(routing.edgeType).toBe("straight");
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

    expect(routing.edgeType).toBe("straight");
    expect([MODIFIED_EDGE_HANDLE_IDS.source.left, MODIFIED_EDGE_HANDLE_IDS.source.bottom]).toContain(routing.sourceHandle);
    expect([MODIFIED_EDGE_HANDLE_IDS.target.right, MODIFIED_EDGE_HANDLE_IDS.target.top]).toContain(routing.targetHandle);
  });

  it("maps handle ids back to the expected React Flow positions", () => {
    expect(getModifiedHandlePosition(MODIFIED_EDGE_HANDLE_IDS.source.left)).toBe(Position.Left);
    expect(getModifiedHandlePosition(MODIFIED_EDGE_HANDLE_IDS.source.right)).toBe(Position.Right);
    expect(getModifiedHandlePosition(MODIFIED_EDGE_HANDLE_IDS.source.top)).toBe(Position.Top);
    expect(getModifiedHandlePosition(MODIFIED_EDGE_HANDLE_IDS.source.bottom)).toBe(Position.Bottom);
  });
});
