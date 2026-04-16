import { Position, type Edge, type Node } from "@xyflow/react";

import type { TopologyObject, TopologyRelation } from "../../api/types";
import type { TopologyCanvasMetrics } from "./canvasConfig";

export const MODIFIED_EDGE_HANDLE_IDS = {
  source: {
    left: "modified-source-left",
    right: "modified-source-right",
    top: "modified-source-top",
    bottom: "modified-source-bottom",
  },
  target: {
    left: "modified-target-left",
    right: "modified-target-right",
    top: "modified-target-top",
    bottom: "modified-target-bottom",
  },
} as const;

export type ModifiedEdgeRouting = {
  edgeType: Edge["type"];
  sourceHandle: string;
  targetHandle: string;
};

export type ModifiedBundleAxis = "horizontal" | "vertical";

export type ModifiedEdgeBundleCandidate = {
  edgeId: string;
  sourceId: string;
  targetId: string;
  sourceHandle: string;
  targetHandle: string;
};

export type ModifiedEdgeBundleMeta = {
  bundleId: string;
  bundleIndex: number;
  bundleSize: number;
  axis: ModifiedBundleAxis;
  laneGap: number;
  mergeRatio: number;
};

export type ModifiedSmoothStepPathOptions = {
  borderRadius: number;
  offset: number;
};

type RoutingDirection = "left" | "right" | "top" | "bottom";

type RoutingInput = {
  sourceNode?: Node;
  targetNode?: Node;
  metrics: TopologyCanvasMetrics;
};

type NodeAnchor = {
  x: number;
  y: number;
};

type BundleGroupMember = {
  edgeId: string;
  axis: ModifiedBundleAxis;
  sourcePrimary: number;
  sourceOrthogonal: number;
};

const MODIFIED_BUNDLE_MIN_COUNT = 4;
const MODIFIED_BUNDLE_SPREAD_MULTIPLIER = 4;
const MODIFIED_BUNDLE_MERGE_RATIO = 0.64;
const MODIFIED_BUNDLE_MIN_GAP = 3.5;
const MODIFIED_BUNDLE_MAX_GAP = 8;

function getNodeCenter(node: Node, metrics: TopologyCanvasMetrics) {
  const width = node.width ?? metrics.nodeWidth;
  const height = node.height ?? metrics.nodeHeight;

  return {
    x: node.position.x + width / 2,
    y: node.position.y + height / 2,
  };
}

function getAnchorOffset(direction: RoutingDirection, metrics: TopologyCanvasMetrics) {
  // Modified nodes are rendered as compact circles near the top of each node card.
  // Anchor edges on the outer circle frame so links visually touch entity boundaries.
  const orbRadius = metrics.nodeCircleSize / 2;
  const centerX = metrics.nodeWidth / 2;
  const centerY = orbRadius;

  switch (direction) {
    case "left":
      return { x: centerX - orbRadius, y: centerY };
    case "right":
      return { x: centerX + orbRadius, y: centerY };
    case "top":
      return { x: centerX, y: 0 };
    case "bottom":
      return { x: centerX, y: metrics.nodeCircleSize };
    default:
      return { x: centerX + orbRadius, y: centerY };
  }
}

function getDirectionFromHandleId(handleId: string | undefined): RoutingDirection {
  if (handleId?.endsWith("left")) {
    return "left";
  }

  if (handleId?.endsWith("right")) {
    return "right";
  }

  if (handleId?.endsWith("top")) {
    return "top";
  }

  if (handleId?.endsWith("bottom")) {
    return "bottom";
  }

  return "right";
}

function getNodeAnchorByHandle(node: Node, handleId: string, metrics: TopologyCanvasMetrics): NodeAnchor {
  const direction = getDirectionFromHandleId(handleId);
  const offset = getAnchorOffset(direction, metrics);
  return {
    x: node.position.x + offset.x,
    y: node.position.y + offset.y,
  };
}

function getSourceHandleId(direction: RoutingDirection) {
  return MODIFIED_EDGE_HANDLE_IDS.source[direction];
}

function getTargetHandleId(direction: RoutingDirection) {
  return MODIFIED_EDGE_HANDLE_IDS.target[direction];
}

function getCandidateDistance(
  sourceNode: Node,
  targetNode: Node,
  sourceDirection: RoutingDirection,
  targetDirection: RoutingDirection,
  metrics: TopologyCanvasMetrics,
) {
  const sourceOffset = getAnchorOffset(sourceDirection, metrics);
  const targetOffset = getAnchorOffset(targetDirection, metrics);

  const sourceX = sourceNode.position.x + sourceOffset.x;
  const sourceY = sourceNode.position.y + sourceOffset.y;
  const targetX = targetNode.position.x + targetOffset.x;
  const targetY = targetNode.position.y + targetOffset.y;

  return Math.hypot(targetX - sourceX, targetY - sourceY);
}

function getPreferredDirection(
  sourceNode: Node,
  targetNode: Node,
  metrics: TopologyCanvasMetrics,
) {
  const sourceTopologyType = (sourceNode.data as any)?.node?.type as TopologyObject["type"] | undefined;
  const targetTopologyType = (targetNode.data as any)?.node?.type as TopologyObject["type"] | undefined;
  const sourceCenter = getNodeCenter(sourceNode, metrics);
  const targetCenter = getNodeCenter(targetNode, metrics);
  const deltaX = targetCenter.x - sourceCenter.x;
  const deltaY = targetCenter.y - sourceCenter.y;
  const absX = Math.abs(deltaX);
  const absY = Math.abs(deltaY);
  const horizontalBias = metrics.nodeWidth * 0.42;
  const verticalBias = metrics.nodeHeight * 0.34;

  if (sourceTopologyType === "cluster") {
    return {
      sourceDirection: "right" as const,
      targetDirection: deltaX >= 0 ? ("left" as const) : ("right" as const),
    };
  }

  if (targetTopologyType === "cluster") {
    return {
      sourceDirection: deltaX >= 0 ? ("left" as const) : ("right" as const),
      targetDirection: "left" as const,
    };
  }

  if (absY <= verticalBias && absX > 0) {
    return deltaX >= 0
      ? { sourceDirection: "right" as const, targetDirection: "left" as const }
      : { sourceDirection: "left" as const, targetDirection: "right" as const };
  }

  if (absX <= horizontalBias && absY > 0) {
    return deltaY >= 0
      ? { sourceDirection: "bottom" as const, targetDirection: "top" as const }
      : { sourceDirection: "top" as const, targetDirection: "bottom" as const };
  }

  if (absX >= absY * 1.12) {
    return deltaX >= 0
      ? { sourceDirection: "right" as const, targetDirection: "left" as const }
      : { sourceDirection: "left" as const, targetDirection: "right" as const };
  }

  if (absY >= absX * 1.12) {
    return deltaY >= 0
      ? { sourceDirection: "bottom" as const, targetDirection: "top" as const }
      : { sourceDirection: "top" as const, targetDirection: "bottom" as const };
  }

  const candidates: Array<{ sourceDirection: RoutingDirection; targetDirection: RoutingDirection }> = [
    { sourceDirection: "right", targetDirection: "left" },
    { sourceDirection: "left", targetDirection: "right" },
    { sourceDirection: "bottom", targetDirection: "top" },
    { sourceDirection: "top", targetDirection: "bottom" },
  ];

  return candidates.reduce((best, candidate) => {
    const bestDistance = getCandidateDistance(
      sourceNode,
      targetNode,
      best.sourceDirection,
      best.targetDirection,
      metrics,
    );
    const candidateDistance = getCandidateDistance(
      sourceNode,
      targetNode,
      candidate.sourceDirection,
      candidate.targetDirection,
      metrics,
    );

    return candidateDistance < bestDistance ? candidate : best;
  });
}

export function deriveModifiedEdgeRouting({
  sourceNode,
  targetNode,
  metrics,
}: RoutingInput): ModifiedEdgeRouting {
  if (!sourceNode || !targetNode) {
    return {
      edgeType: "default",
      sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
      targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
    };
  }

  const { sourceDirection, targetDirection } = getPreferredDirection(sourceNode, targetNode, metrics);

  return {
    edgeType: "default",
    sourceHandle: getSourceHandleId(sourceDirection),
    targetHandle: getTargetHandleId(targetDirection),
  };
}

function deriveBundleAxis(targetHandle: string) {
  const direction = getDirectionFromHandleId(targetHandle);
  return direction === "left" || direction === "right" ? "horizontal" : "vertical";
}

function resolveOrthogonalSpread(members: BundleGroupMember[]) {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;

  members.forEach((member) => {
    min = Math.min(min, member.sourceOrthogonal);
    max = Math.max(max, member.sourceOrthogonal);
  });

  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return Number.POSITIVE_INFINITY;
  }

  return max - min;
}

function getBundleSpreadThreshold(axis: ModifiedBundleAxis, metrics: TopologyCanvasMetrics) {
  return (axis === "horizontal" ? metrics.nodeHeight : metrics.nodeWidth) * MODIFIED_BUNDLE_SPREAD_MULTIPLIER;
}

function getBundleLaneGap(bundleSize: number) {
  const adaptiveGap = 16 / Math.sqrt(bundleSize);
  return Math.max(MODIFIED_BUNDLE_MIN_GAP, Math.min(MODIFIED_BUNDLE_MAX_GAP, adaptiveGap));
}

export function deriveModifiedEdgeBundles({
  edges,
  nodeLookup,
  metrics,
}: {
  edges: ModifiedEdgeBundleCandidate[];
  nodeLookup: Map<string, Node>;
  metrics: TopologyCanvasMetrics;
}) {
  const groupedMembers = new Map<string, BundleGroupMember[]>();

  edges.forEach((edge) => {
    const sourceNode = nodeLookup.get(edge.sourceId);
    const targetNode = nodeLookup.get(edge.targetId);
    if (!sourceNode || !targetNode) {
      return;
    }

    const sourceAnchor = getNodeAnchorByHandle(sourceNode, edge.sourceHandle, metrics);
    const targetAnchor = getNodeAnchorByHandle(targetNode, edge.targetHandle, metrics);
    const axis = deriveBundleAxis(edge.targetHandle);
    const sourcePrimary = axis === "horizontal" ? sourceAnchor.x : sourceAnchor.y;
    const sourceOrthogonal = axis === "horizontal" ? sourceAnchor.y : sourceAnchor.x;
    const targetHandleKey = edge.targetHandle || "target-auto";
    const bundleKey = `${edge.targetId}|${targetHandleKey}|${axis}|${Math.round(targetAnchor.x)}|${Math.round(targetAnchor.y)}`;
    const members = groupedMembers.get(bundleKey);

    const member: BundleGroupMember = {
      edgeId: edge.edgeId,
      axis,
      sourcePrimary,
      sourceOrthogonal,
    };

    if (members) {
      members.push(member);
      return;
    }

    groupedMembers.set(bundleKey, [member]);
  });

  const bundleMetadata = new Map<string, ModifiedEdgeBundleMeta>();

  groupedMembers.forEach((members, bundleId) => {
    if (members.length < MODIFIED_BUNDLE_MIN_COUNT) {
      return;
    }

    const axis = members[0].axis;
    const spread = resolveOrthogonalSpread(members);
    const threshold = getBundleSpreadThreshold(axis, metrics);

    if (spread > threshold) {
      return;
    }

    const sortedMembers = [...members].sort((left, right) => {
      if (left.sourceOrthogonal !== right.sourceOrthogonal) {
        return left.sourceOrthogonal - right.sourceOrthogonal;
      }

      if (left.sourcePrimary !== right.sourcePrimary) {
        return left.sourcePrimary - right.sourcePrimary;
      }

      return left.edgeId.localeCompare(right.edgeId);
    });

    const laneGap = getBundleLaneGap(sortedMembers.length);

    sortedMembers.forEach((member, index) => {
      bundleMetadata.set(member.edgeId, {
        bundleId,
        bundleIndex: index,
        bundleSize: sortedMembers.length,
        axis,
        laneGap,
        mergeRatio: MODIFIED_BUNDLE_MERGE_RATIO,
      });
    });
  });

  return bundleMetadata;
}

type ModifiedPathOptionsInput = {
  sourceType?: TopologyObject["type"];
  targetType?: TopologyObject["type"];
  relationType: TopologyRelation["relationType"];
  isAggregated?: boolean;
};

function getSortedTypePair(
  sourceType?: TopologyObject["type"],
  targetType?: TopologyObject["type"],
) {
  if (!sourceType || !targetType) {
    return undefined;
  }

  return [sourceType, targetType].sort((left, right) => left.localeCompare(right)).join(":");
}

// Kept for backwards compatibility with existing imports/tests.
export function deriveModifiedSmoothStepPathOptions({
  sourceType,
  targetType,
  relationType,
  isAggregated,
}: ModifiedPathOptionsInput): ModifiedSmoothStepPathOptions {
  const pairKey = getSortedTypePair(sourceType, targetType);

  if (pairKey === "port:switch" || pairKey === "cluster:switch") {
    return {
      borderRadius: 36,
      offset: 20,
    };
  }

  if (pairKey === "bmc:node" || pairKey === "gpu:node") {
    return {
      borderRadius: 14,
      offset: 8,
    };
  }

  if (pairKey === "node:service" || pairKey === "pod:service") {
    return {
      borderRadius: 22,
      offset: 12,
    };
  }

  if (isAggregated || relationType === "aggregated") {
    return {
      borderRadius: 30,
      offset: 18,
    };
  }

  return {
    borderRadius: 26,
    offset: 14,
  };
}

export function getModifiedHandlePosition(handleId: string) {
  if (handleId.endsWith("left")) {
    return Position.Left;
  }

  if (handleId.endsWith("right")) {
    return Position.Right;
  }

  if (handleId.endsWith("top")) {
    return Position.Top;
  }

  return Position.Bottom;
}
