import { describe, expect, it } from "vitest";

import { shouldShowModifiedEdgeLabel } from "./TopologyCanvas";

describe("shouldShowModifiedEdgeLabel", () => {
  it("returns false when there is no selected node and no active edge", () => {
    expect(
      shouldShowModifiedEdgeLabel({
        edgeId: "e1",
        sourceId: "a",
        targetId: "b",
      }),
    ).toBe(false);
  });

  it("returns true when edge is connected to selected node", () => {
    expect(
      shouldShowModifiedEdgeLabel({
        selectedNodeId: "a",
        edgeId: "e1",
        sourceId: "a",
        targetId: "b",
      }),
    ).toBe(true);
  });

  it("returns false when selected node is unrelated", () => {
    expect(
      shouldShowModifiedEdgeLabel({
        selectedNodeId: "x",
        edgeId: "e1",
        sourceId: "a",
        targetId: "b",
      }),
    ).toBe(false);
  });

  it("returns true when edge is the active clicked edge", () => {
    expect(
      shouldShowModifiedEdgeLabel({
        activeEdgeId: "e1",
        edgeId: "e1",
        sourceId: "a",
        targetId: "b",
      }),
    ).toBe(true);
  });

  it("uses union semantics when node selection and active edge coexist", () => {
    expect(
      shouldShowModifiedEdgeLabel({
        selectedNodeId: "a",
        activeEdgeId: "e2",
        edgeId: "e2",
        sourceId: "x",
        targetId: "y",
      }),
    ).toBe(true);

    expect(
      shouldShowModifiedEdgeLabel({
        selectedNodeId: "a",
        activeEdgeId: "e2",
        edgeId: "e3",
        sourceId: "x",
        targetId: "y",
      }),
    ).toBe(false);
  });
});
