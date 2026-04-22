import { describe, expect, it } from "vitest";

import {
  getExpandedAggregateMemberIdSet,
  getGroupExpansionNodeVisualState,
  shouldShowModifiedEdgeLabel,
} from "./TopologyCanvas";

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

describe("expanded aggregate node visual state", () => {
  it("collects member ids only from currently expanded aggregates", () => {
    const members = getExpandedAggregateMemberIdSet(["aggregate:gpu-1"], {
      "aggregate:gpu-1": {
        aggregateId: "aggregate:gpu-1",
        label: "GPU group 1",
        memberIds: ["gpu-a", "gpu-b"],
      },
      "aggregate:gpu-2": {
        aggregateId: "aggregate:gpu-2",
        label: "GPU group 2",
        memberIds: ["gpu-c"],
      },
    });

    expect(Array.from(members).sort()).toEqual(["gpu-a", "gpu-b"]);
  });

  it("highlights member nodes and dims non-member nodes when an aggregate is expanded", () => {
    const members = new Set(["gpu-a", "gpu-b"]);

    expect(
      getGroupExpansionNodeVisualState({
        nodeId: "gpu-a",
        hasExpandedAggregate: true,
        expandedMemberIdSet: members,
      }),
    ).toEqual({
      isGroupMember: true,
      dimmedByExpandedGroup: false,
      zIndex: 6,
    });

    expect(
      getGroupExpansionNodeVisualState({
        nodeId: "bmc-1",
        hasExpandedAggregate: true,
        expandedMemberIdSet: members,
      }),
    ).toEqual({
      isGroupMember: false,
      dimmedByExpandedGroup: true,
      zIndex: 1,
    });
  });

  it("does not alter visual state when no aggregate is expanded", () => {
    expect(
      getGroupExpansionNodeVisualState({
        nodeId: "gpu-a",
        hasExpandedAggregate: false,
        expandedMemberIdSet: new Set(["gpu-a"]),
      }),
    ).toEqual({
      isGroupMember: false,
      dimmedByExpandedGroup: false,
      zIndex: undefined,
    });
  });
});
