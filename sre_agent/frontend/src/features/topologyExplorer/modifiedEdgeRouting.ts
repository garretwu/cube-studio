import { Position, type Edge, type Node } from "@xyflow/react";

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

type RoutingDirection = "left" | "right" | "top" | "bottom";

type RoutingInput = {
  sourceNode?: Node;
  targetNode?: Node;
  metrics: TopologyCanvasMetrics;
};

function getNodeCenter(node: Node, metrics: TopologyCanvasMetrics) {
  const width = node.width ?? metrics.nodeWidth;
  const height = node.height ?? metrics.nodeHeight;

  return {
    x: node.position.x + width / 2,
    y: node.position.y + height / 2,
  };
}

function getAnchorOffset(direction: RoutingDirection, metrics: TopologyCanvasMetrics) {
  const orbRadius = metrics.nodeCircleSize / 2;
  const verticalCenter = orbRadius + 2;
  const horizontalCenter = metrics.nodeWidth / 2;

  switch (direction) {
    case "left":
      return { x: 0, y: verticalCenter };
    case "right":
      return { x: metrics.nodeWidth, y: verticalCenter };
    case "top":
      return { x: horizontalCenter, y: 4 };
    case "bottom":
      return { x: horizontalCenter, y: metrics.nodeCircleSize + 12 };
    default:
      return { x: metrics.nodeWidth, y: verticalCenter };
  }
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
  const sourceCenter = getNodeCenter(sourceNode, metrics);
  const targetCenter = getNodeCenter(targetNode, metrics);
  const deltaX = targetCenter.x - sourceCenter.x;
  const deltaY = targetCenter.y - sourceCenter.y;
  const absX = Math.abs(deltaX);
  const absY = Math.abs(deltaY);
  const horizontalBias = metrics.nodeWidth * 0.42;
  const verticalBias = metrics.nodeHeight * 0.34;

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
      edgeType: "straight",
      sourceHandle: MODIFIED_EDGE_HANDLE_IDS.source.right,
      targetHandle: MODIFIED_EDGE_HANDLE_IDS.target.left,
    };
  }

  const { sourceDirection, targetDirection } = getPreferredDirection(sourceNode, targetNode, metrics);

  return {
    edgeType: "straight",
    sourceHandle: getSourceHandleId(sourceDirection),
    targetHandle: getTargetHandleId(targetDirection),
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
