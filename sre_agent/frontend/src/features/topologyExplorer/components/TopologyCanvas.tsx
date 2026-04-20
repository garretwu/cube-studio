import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  getBezierPath,
  type Edge,
  type EdgeProps,
  type EdgeTypes,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";

import type { TopologyObject, TopologyRelation } from "../../../api/types";
import { AppIcon } from "../../../components/ui";
import {
  formatRelationType,
  formatTopologyStatus,
  formatTopologyType,
  getTopologyTypeIconAsset,
} from "../formatters";
import { isSyntheticGpuAggregateNode, isSyntheticServiceAggregateNode } from "../selectors";
import {
  DEFAULT_FIT_PADDING,
  FOCUSED_FIT_PADDING,
  TOPOLOGY_CANVAS_METRICS,
  type TopologyCanvasMetrics,
  type TopologyCanvasVariant,
} from "../canvasConfig";
import { computeModifiedHybridLayout } from "../modifiedLayout";
import {
  deriveModifiedEdgeBundles,
  deriveModifiedEdgeRouting,
  getModifiedHandlePosition,
  MODIFIED_EDGE_HANDLE_IDS,
  type ModifiedEdgeBundleMeta,
} from "../modifiedEdgeRouting";
import type { ExplorerLayoutPreset } from "../types";

type ExpandedAggregateMetaEntry = {
  aggregateId: string;
  label: string;
  memberIds: string[];
};

type ExpandedAggregateMetaMap = Record<string, ExpandedAggregateMetaEntry>;

type TopologyCanvasProps = {
  nodes: TopologyObject[];
  edges: TopologyRelation[];
  selectedNodeId?: string;
  hoveredNodeId?: string;
  highlightedTypes?: TopologyObject["type"][];
  expandedAggregateIds?: string[];
  expandedAggregateMeta?: ExpandedAggregateMetaMap;
  onCollapseAggregate?: (aggregateId: string) => void;
  forceEdgeLabels?: boolean;
  matchedNodeIds: string[];
  neighborDepths: Map<string, number>;
  layoutPreset: ExplorerLayoutPreset;
  variant?: TopologyCanvasVariant;
  onSelectNode: (nodeId: string) => void;
  onHoverNode: (nodeId?: string) => void;
  onZoomChange?: (zoomPercent: number) => void;
  onOpenNodeActions?: (payload: TopologyCanvasNodeAction) => void;
  onCanvasInteraction?: () => void;
};

export type TopologyCanvasHandle = {
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  recenter: (nodeId?: string) => void;
  focusNode: (nodeId: string) => void;
};

export type TopologyCanvasNodeAction = {
  node: TopologyObject;
  clientX: number;
  clientY: number;
};

type ExplorerFlowNodeData = {
  node: TopologyObject;
  selected: boolean;
  searchHit: boolean;
  dimmed: boolean;
  isGroupMember: boolean;
  neighborDepth: number;
  metrics: TopologyCanvasMetrics;
  variant: TopologyCanvasVariant;
  onSelectNode: (nodeId: string) => void;
};

type ExplorerFlowEdgeData = {
  bundleMeta?: ModifiedEdgeBundleMeta;
};

type GroupBoxData = {
  aggregateId: string;
  label: string;
  count: number;
  onCollapse?: (aggregateId: string) => void;
};

type CanvasNodeData = ExplorerFlowNodeData | GroupBoxData;

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function buildModifiedBundledPath({
  sourceX,
  sourceY,
  targetX,
  targetY,
  bundleMeta,
}: {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  bundleMeta: ModifiedEdgeBundleMeta;
}) {
  const centerIndex = (bundleMeta.bundleSize - 1) / 2;
  const laneOffset = (bundleMeta.bundleIndex - centerIndex) * bundleMeta.laneGap;

  if (bundleMeta.axis === "horizontal") {
    const rawMergeX = sourceX + (targetX - sourceX) * bundleMeta.mergeRatio;
    const minMergeX = Math.min(sourceX, targetX) + 6;
    const maxMergeX = Math.max(sourceX, targetX) - 6;
    const mergeX = clamp(rawMergeX, minMergeX, maxMergeX);
    const mergeY = targetY + laneOffset;
    const firstDx = mergeX - sourceX;
    const secondDx = targetX - mergeX;

    return {
      path: [
        `M ${sourceX},${sourceY}`,
        `C ${sourceX + firstDx * 0.38},${sourceY} ${mergeX - firstDx * 0.24},${mergeY} ${mergeX},${mergeY}`,
        `C ${mergeX + secondDx * 0.2},${mergeY} ${targetX - secondDx * 0.42},${targetY} ${targetX},${targetY}`,
      ].join(" "),
      labelX: (mergeX + targetX) / 2,
      labelY: (mergeY + targetY) / 2,
    };
  }

  const rawMergeY = sourceY + (targetY - sourceY) * bundleMeta.mergeRatio;
  const minMergeY = Math.min(sourceY, targetY) + 6;
  const maxMergeY = Math.max(sourceY, targetY) - 6;
  const mergeY = clamp(rawMergeY, minMergeY, maxMergeY);
  const mergeX = targetX + laneOffset;
  const firstDy = mergeY - sourceY;
  const secondDy = targetY - mergeY;

  return {
    path: [
      `M ${sourceX},${sourceY}`,
      `C ${sourceX},${sourceY + firstDy * 0.38} ${mergeX},${mergeY - firstDy * 0.24} ${mergeX},${mergeY}`,
      `C ${mergeX},${mergeY + secondDy * 0.2} ${targetX},${targetY - secondDy * 0.42} ${targetX},${targetY}`,
    ].join(" "),
    labelX: (mergeX + targetX) / 2,
    labelY: (mergeY + targetY) / 2,
  };
}

function ModifiedBundledEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  markerEnd,
  style,
  interactionWidth,
  label,
  labelStyle,
  labelShowBg,
  labelBgStyle,
  labelBgBorderRadius,
  labelBgPadding,
  data,
}: EdgeProps<Edge<ExplorerFlowEdgeData>>) {
  const bundleMeta = data?.bundleMeta;

  if (!bundleMeta) {
    const [edgePath, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY });
    return (
      <BaseEdge
        interactionWidth={interactionWidth}
        label={label}
        labelBgBorderRadius={labelBgBorderRadius}
        labelBgPadding={labelBgPadding}
        labelBgStyle={labelBgStyle}
        labelShowBg={labelShowBg}
        labelStyle={labelStyle}
        labelX={labelX}
        labelY={labelY}
        markerEnd={markerEnd}
        path={edgePath}
        style={style}
      />
    );
  }

  const { path, labelX, labelY } = buildModifiedBundledPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    bundleMeta,
  });

  return (
    <BaseEdge
      interactionWidth={interactionWidth}
      label={label}
      labelBgBorderRadius={labelBgBorderRadius}
      labelBgPadding={labelBgPadding}
      labelBgStyle={labelBgStyle}
      labelShowBg={labelShowBg}
      labelStyle={labelStyle}
      labelX={labelX}
      labelY={labelY}
      markerEnd={markerEnd}
      path={path}
      style={style}
    />
  );
}

const HANDLE_STYLE = {
  width: 10,
  height: 10,
  opacity: 0,
  background: "transparent",
  border: "none",
} as const;

function getEdgeColor(edge: TopologyRelation, variant: TopologyCanvasVariant) {
  if (edge.status === "abnormal") {
    return variant === "modified" ? "#d65454" : "#c85656";
  }

  if (edge.status === "impacted") {
    return variant === "modified" ? "#c38c2f" : "#b88230";
  }

  if (edge.isCritical) {
    return variant === "modified" ? "#315efb" : "#4d5ee9";
  }

  return edge.isAggregated ? "#9aa7bb" : variant === "modified" ? "#c4cedc" : "#c4cad6";
}

function getAggregateCount(node: TopologyObject) {
  const aggregateCount = Number(node.attributes.aggregateCount ?? 0);
  return Number.isFinite(aggregateCount) ? aggregateCount : 0;
}

function getAggregateTypeLabel(node: TopologyObject) {
  const rawType = String(node.attributes.aggregateRawType ?? node.attributes.rawType ?? node.type);
  if (rawType === "gpu") {
    return "GPU";
  }
  return rawType === "pod" ? "Pod" : "\u670d\u52a1\u7ec4";
}

function getNodeSummary(node: TopologyObject) {
  if (isSyntheticServiceAggregateNode(node) || isSyntheticGpuAggregateNode(node)) {
    return `\u805a\u5408 ${getAggregateCount(node)} \u4e2a${getAggregateTypeLabel(node)}\u5bf9\u8c61\uff0c\u70b9\u51fb\u5c55\u5f00\u67e5\u770b\u3002`;
  }

  return node.summary;
}

export function getExpandedAggregateMemberIdSet(
  expandedAggregateIds: string[] | undefined,
  expandedAggregateMeta: ExpandedAggregateMetaMap | undefined,
) {
  const memberIdSet = new Set<string>();
  const aggregateIds = expandedAggregateIds ?? [];
  const meta = expandedAggregateMeta ?? {};

  aggregateIds.forEach((aggregateId) => {
    const entry = meta[aggregateId];
    if (!entry) {
      return;
    }
    entry.memberIds.forEach((memberId) => memberIdSet.add(memberId));
  });

  return memberIdSet;
}

export function getGroupExpansionNodeVisualState({
  nodeId,
  hasExpandedAggregate,
  expandedMemberIdSet,
}: {
  nodeId: string;
  hasExpandedAggregate: boolean;
  expandedMemberIdSet: Set<string>;
}) {
  if (!hasExpandedAggregate) {
    return {
      isGroupMember: false,
      dimmedByExpandedGroup: false,
      zIndex: undefined as number | undefined,
    };
  }

  const isGroupMember = expandedMemberIdSet.has(nodeId);
  return {
    isGroupMember,
    dimmedByExpandedGroup: !isGroupMember,
    zIndex: isGroupMember ? 6 : 1,
  };
}
export function shouldShowModifiedEdgeLabel({
  selectedNodeId,
  activeEdgeId,
  edgeId,
  sourceId,
  targetId,
}: {
  selectedNodeId?: string;
  activeEdgeId?: string;
  edgeId: string;
  sourceId: string;
  targetId: string;
}) {
  const isConnectedToSelected = Boolean(selectedNodeId) && (sourceId === selectedNodeId || targetId === selectedNodeId);
  const isActiveEdge = activeEdgeId === edgeId;
  return Boolean((selectedNodeId && isConnectedToSelected) || (activeEdgeId && isActiveEdge));
}

function getBasePositions(
  nodes: TopologyObject[],
  layoutPreset: ExplorerLayoutPreset,
  metrics: TopologyCanvasMetrics,
) {
  const positions = new Map<string, { x: number; y: number }>();
  const perLayer = new Map<string, number>();
  const perType = new Map<string, number>();
  const layerOrder: Record<TopologyObject["layer"], number> = {
    physical: 0,
    network: 1,
    compute: 2,
    service: 3,
  };

  nodes.forEach((node) => {
    if (layoutPreset === "layered") {
      const columnIndex = layerOrder[node.layer];
      const nextIndex = perLayer.get(node.layer) ?? 0;
      positions.set(node.id, {
        x: metrics.layerXOffset + columnIndex * metrics.layerXSpacing,
        y: metrics.layerYOffset + nextIndex * metrics.layerYSpacing,
      });
      perLayer.set(node.layer, nextIndex + 1);
      return;
    }

    const anchor = metrics.typeOrder[node.type];
    const nextIndex = perType.get(node.type) ?? 0;
    positions.set(node.id, {
      x: anchor.x + metrics.typeXOffset,
      y: anchor.y + metrics.typeYOffset + nextIndex * metrics.typeYSpacing,
    });
    perType.set(node.type, nextIndex + 1);
  });

  return positions;
}

function getModifiedHandleStyle(position: Position, metrics: TopologyCanvasMetrics) {
  const orbRadius = metrics.nodeCircleSize / 2;
  const orbCenterX = metrics.nodeWidth / 2;
  const orbCenterY = orbRadius;

  if (position === Position.Left) {
    return {
      ...HANDLE_STYLE,
      left: orbCenterX - orbRadius,
      top: orbCenterY,
      transform: "translate(-50%, -50%)",
    };
  }

  if (position === Position.Right) {
    return {
      ...HANDLE_STYLE,
      left: orbCenterX + orbRadius,
      top: orbCenterY,
      transform: "translate(-50%, -50%)",
    };
  }

  if (position === Position.Top) {
    return {
      ...HANDLE_STYLE,
      left: orbCenterX,
      top: 0,
      transform: "translate(-50%, -50%)",
    };
  }

  return {
    ...HANDLE_STYLE,
    left: orbCenterX,
    top: metrics.nodeCircleSize,
    transform: "translate(-50%, -50%)",
  };
}

function ExplorerNode({ data }: NodeProps<Node<ExplorerFlowNodeData>>) {
  const { node, selected, searchHit, dimmed, isGroupMember, neighborDepth, metrics, variant, onSelectNode } = data;
  const isAggregate = isSyntheticServiceAggregateNode(node) || isSyntheticGpuAggregateNode(node);
  const aggregateCount = getAggregateCount(node);
  const handleStyle = { ...HANDLE_STYLE, top: metrics.nodeCircleSize / 2 + 2 };
  const statusLabel = formatTopologyStatus(node.status);
  const semanticTypeLabel = isAggregate ? `${getAggregateTypeLabel(node)}\u805a\u5408\u7ec4` : formatTopologyType(node.type);
  const title = `${node.name} | ${semanticTypeLabel} | ${statusLabel}${neighborDepth > 0 ? ` | ${neighborDepth} hop` : ""}`;

  if (variant === "modified") {
    return (
      <div
        className={[
          `topology-flow-node topology-flow-node--${node.type}`,
          `topology-flow-node--${node.status}`,
          "topology-flow-node--minimal",
          selected ? "topology-flow-node--selected" : "",
          searchHit ? "topology-flow-node--search-hit" : "",
          dimmed ? "topology-flow-node--dimmed" : "",
          isAggregate ? "topology-flow-node--aggregate" : "",
          isGroupMember ? "topology-flow-node--group-member" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        title={`${node.name} | ${getNodeSummary(node)}`}
      >
        {Object.values(MODIFIED_EDGE_HANDLE_IDS.target).map((handleId) => {
          const position = getModifiedHandlePosition(handleId);
          return (
            <Handle
              id={handleId}
              key={handleId}
              position={position}
              style={getModifiedHandleStyle(position, metrics)}
              type="target"
            />
          );
        })}
        <button aria-label={title} className="topology-flow-node__button" onClick={() => onSelectNode(node.id)} type="button">
          <span className="topology-flow-node__orb" aria-hidden="true">
            <img
              alt=""
              className="topology-flow-node__icon-image"
              draggable={false}
              src={getTopologyTypeIconAsset(node.type)}

            />
            {isAggregate ? <span className="topology-flow-node__count-badge">{aggregateCount}</span> : null}
          </span>
          <span className="topology-flow-node__title">{node.name}</span>
          {isAggregate ? <span className="topology-flow-node__meta" aria-hidden="true" /> : null}
        </button>
        {Object.values(MODIFIED_EDGE_HANDLE_IDS.source).map((handleId) => {
          const position = getModifiedHandlePosition(handleId);
          return (
            <Handle
              id={handleId}
              key={handleId}
              position={position}
              style={getModifiedHandleStyle(position, metrics)}
              type="source"
            />
          );
        })}
      </div>
    );
  }

  return (
    <div
      className={[
        `topology-flow-node topology-flow-node--${node.type}`,
        `topology-flow-node--${node.status}`,
        selected ? "topology-flow-node--selected" : "",
        searchHit ? "topology-flow-node--search-hit" : "",
        dimmed ? "topology-flow-node--dimmed" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      title={`${node.name} | ${node.summary}`}
    >
      <Handle position={Position.Left} style={handleStyle} type="target" />
      <button aria-label={title} className="topology-flow-node__button" onClick={() => onSelectNode(node.id)} type="button">
        <span className="topology-flow-node__orb" aria-hidden="true">
          <img
            alt=""
            className="topology-flow-node__icon-image"
            draggable={false}
            src={getTopologyTypeIconAsset(node.type)}
            style={{ width: metrics.iconSize + 4, height: metrics.iconSize + 4 }}
          />
        </span>
        <span className="topology-flow-node__title">{node.name}</span>
      </button>
      <Handle position={Position.Right} style={handleStyle} type="source" />
    </div>
  );
}

const nodeTypes = {
  assetNode: ExplorerNode,
  groupBox: function GroupBoxNode({ data }: NodeProps<Node<GroupBoxData>>) {
    const { aggregateId, label, count, onCollapse } = data;
    const handleCollapse = () => onCollapse?.(aggregateId);
    return (
      <button
        className="topology-aggregate-box"
        onClick={handleCollapse}
        type="button"
      >
        <div className="topology-aggregate-box__header">
          <div className="topology-aggregate-box__title">
            <span className="topology-aggregate-box__label">{label}</span>
            <span className="topology-aggregate-box__count">{count}</span>
          </div>
          <span className="topology-aggregate-box__collapse" aria-hidden="true">
            <AppIcon name="frameCollapse" size={14} />
          </span>
        </div>
      </button>
    );
  },
};

const edgeTypes: EdgeTypes = {
  modifiedBundled: ModifiedBundledEdge,
};

const isJsdomEnvironment = typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);
const shouldRenderBackground = typeof navigator === "undefined" || !isJsdomEnvironment;

const TopologyCanvas = forwardRef<TopologyCanvasHandle, TopologyCanvasProps>(function TopologyCanvas(
  {
    nodes,
    edges,
    selectedNodeId,
    hoveredNodeId,
    highlightedTypes,
    expandedAggregateIds,
    expandedAggregateMeta,
    onCollapseAggregate,
    forceEdgeLabels = false,
    matchedNodeIds,
    neighborDepths,
    layoutPreset,
    variant = "default",
    onSelectNode,
    onHoverNode,
    onZoomChange,
    onOpenNodeActions,
    onCanvasInteraction,
  },
  ref,
) {
  const [instance, setInstance] = useState<ReactFlowInstance<Node<CanvasNodeData>, Edge<ExplorerFlowEdgeData>> | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; title: string; summary: string } | null>(null);
  const [activeEdgeId, setActiveEdgeId] = useState<string | undefined>(undefined);
  const canvasHostRef = useRef<HTMLDivElement | null>(null);
  const positionsRef = useRef<Record<string, { x: number; y: number }>>({});
  const flowNodesRef = useRef<Node<ExplorerFlowNodeData>[]>([]);
  const timeoutHandlesRef = useRef<number[]>([]);
  const layoutRef = useRef(layoutPreset);
  const variantRef = useRef<TopologyCanvasVariant>(variant);
  const nodeKey = nodes.map((node) => node.id).join("|");
  const nodeKeyRef = useRef(nodeKey);
  const focusNodeId = selectedNodeId ?? hoveredNodeId;
  const metrics = TOPOLOGY_CANVAS_METRICS[variant];
  const highlightSet = useMemo(() => new Set(highlightedTypes ?? []), [highlightedTypes]);
  const hasTypeHighlight = (highlightedTypes?.length ?? 0) > 0;
  const expandedMemberIdSet = useMemo(
    () => getExpandedAggregateMemberIdSet(expandedAggregateIds, expandedAggregateMeta),
    [expandedAggregateIds, expandedAggregateMeta],
  );
  const hasExpandedAggregate = expandedMemberIdSet.size > 0;
  const nodeTypeById = useMemo(() => new Map(nodes.map((node) => [node.id, node.type] as const)), [nodes]);

  if (layoutRef.current !== layoutPreset || nodeKeyRef.current !== nodeKey || variantRef.current !== variant) {
    positionsRef.current = {};
    layoutRef.current = layoutPreset;
    nodeKeyRef.current = nodeKey;
    variantRef.current = variant;
  }

  const layoutPositions = useMemo(
    () =>
      variant === "modified"
        ? computeModifiedHybridLayout(nodes, edges, layoutPreset, metrics)
        : getBasePositions(nodes, layoutPreset, metrics),
    [edges, layoutPreset, metrics, nodes, variant],
  );

  const flowNodes = useMemo<Node<ExplorerFlowNodeData>[]>(() => {
    return nodes.map((node) => {
      const position = positionsRef.current[node.id] ?? layoutPositions.get(node.id) ?? { x: 0, y: 0 };
      const neighborDepth = neighborDepths.get(node.id) ?? -1;
      const inSelectionChain = neighborDepths.has(node.id);
      const dimmedBySelection = Boolean(selectedNodeId) && node.id !== selectedNodeId && !inSelectionChain;
      const dimmedByType = hasTypeHighlight && !highlightSet.has(node.type);
      const groupVisualState = getGroupExpansionNodeVisualState({
        nodeId: node.id,
        hasExpandedAggregate,
        expandedMemberIdSet,
      });
      const dimmed = dimmedBySelection || dimmedByType || groupVisualState.dimmedByExpandedGroup;

      return {
        id: node.id,
        type: "assetNode",
        position,
        draggable: true,
        zIndex: groupVisualState.zIndex,
        width: metrics.nodeWidth,
        height: metrics.nodeHeight,
        data: {
          node,
          selected: node.id === selectedNodeId,
          searchHit: matchedNodeIds.includes(node.id),
          dimmed,
          isGroupMember: groupVisualState.isGroupMember,
          neighborDepth,
          metrics,
          variant,
          onSelectNode,
        },
      };
    });
  }, [
    expandedMemberIdSet,
    hasExpandedAggregate,
    hasTypeHighlight,
    highlightSet,
    layoutPositions,
    matchedNodeIds,
    metrics,
    neighborDepths,
    nodes,
    onSelectNode,
    selectedNodeId,
    variant,
  ]);

  const flowNodeLookup = useMemo(() => new Map(flowNodes.map((node) => [node.id, node])), [flowNodes]);

  const groupBoxNodes = useMemo<Node<GroupBoxData>[]>(() => {
    const aggregateIds = expandedAggregateIds ?? [];
    const meta = expandedAggregateMeta ?? {};
    if (aggregateIds.length === 0) {
      return [];
    }

    const padding = 18;
    const headerHeight = 32;

    const result: Array<Node<GroupBoxData>> = [];

    aggregateIds.forEach((aggregateId) => {
      const entry = meta[aggregateId];
      if (!entry) {
        return;
      }

      const memberNodes = entry.memberIds
        .map((memberId) => flowNodeLookup.get(memberId))
        .filter((node): node is Node<ExplorerFlowNodeData> => Boolean(node));
      if (memberNodes.length === 0) {
        return;
      }

      const minX = Math.min(...memberNodes.map((node) => node.position.x));
      const minY = Math.min(...memberNodes.map((node) => node.position.y));
      const maxX = Math.max(...memberNodes.map((node) => node.position.x + metrics.nodeWidth));
      const maxY = Math.max(...memberNodes.map((node) => node.position.y + metrics.nodeHeight));

      const x = Math.round(minX - padding);
      const y = Math.round(minY - padding - headerHeight);
      const width = Math.round(Math.max(240, maxX - minX + padding * 2));
      const height = Math.round(Math.max(140, maxY - minY + padding * 2 + headerHeight));

      result.push({
        id: `groupbox:${entry.aggregateId}`,
        type: "groupBox",
        position: { x, y },
        draggable: false,
        selectable: false,
        focusable: false,
        connectable: false,
        data: {
          aggregateId: entry.aggregateId,
          label: entry.label,
          count: memberNodes.length,
          onCollapse: onCollapseAggregate,
        },
        style: {
          width,
          height,
          zIndex: 4,
        },
      });
    });

    return result;
  }, [expandedAggregateIds, expandedAggregateMeta, flowNodeLookup, metrics.nodeHeight, metrics.nodeWidth, onCollapseAggregate]);

  const flowEdges = useMemo<Edge<ExplorerFlowEdgeData>[]>(() => {
    const routingByEdgeId = new Map<string, ReturnType<typeof deriveModifiedEdgeRouting>>();

    if (variant === "modified") {
      edges.forEach((edge) => {
        routingByEdgeId.set(
          edge.id,
          deriveModifiedEdgeRouting({
            sourceNode: flowNodeLookup.get(edge.source),
            targetNode: flowNodeLookup.get(edge.target),
            metrics,
          }),
        );
      });
    }

    const bundleMetadataByEdgeId =
      variant === "modified"
        ? deriveModifiedEdgeBundles({
            edges: edges.map((edge) => {
              const routing = routingByEdgeId.get(edge.id);
              return {
                edgeId: edge.id,
                sourceId: edge.source,
                targetId: edge.target,
                sourceHandle: routing?.sourceHandle ?? MODIFIED_EDGE_HANDLE_IDS.source.right,
                targetHandle: routing?.targetHandle ?? MODIFIED_EDGE_HANDLE_IDS.target.left,
              };
            }),
            nodeLookup: flowNodeLookup,
            metrics,
          })
        : new Map<string, ModifiedEdgeBundleMeta>();

    return edges.map((edge) => {
      const isConnectedToFocus = Boolean(focusNodeId) && (edge.source === focusNodeId || edge.target === focusNodeId);
      const isContextEdge =
        !focusNodeId ||
        edge.source === focusNodeId ||
        edge.target === focusNodeId ||
        (neighborDepths.has(edge.source) && neighborDepths.has(edge.target));
      const sourceType = nodeTypeById.get(edge.source);
      const targetType = nodeTypeById.get(edge.target);
      const isTypeMatched =
        !hasTypeHighlight ||
        (Boolean(sourceType && highlightSet.has(sourceType)) && Boolean(targetType && highlightSet.has(targetType)));
      const stroke = getEdgeColor(edge, variant);
      const shouldShowLabel =
        variant === "modified"
          ? shouldShowModifiedEdgeLabel({
              selectedNodeId,
              activeEdgeId,
              edgeId: edge.id,
              sourceId: edge.source,
              targetId: edge.target,
            })
          : forceEdgeLabels || isConnectedToFocus;
      const routing =
        variant === "modified"
          ? routingByEdgeId.get(edge.id) ??
            deriveModifiedEdgeRouting({
              sourceNode: flowNodeLookup.get(edge.source),
              targetNode: flowNodeLookup.get(edge.target),
              metrics,
            })
          : undefined;
      const bundleMeta = variant === "modified" ? bundleMetadataByEdgeId.get(edge.id) : undefined;

      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: bundleMeta ? "modifiedBundled" : routing?.edgeType ?? "default",
        sourceHandle: routing?.sourceHandle,
        targetHandle: routing?.targetHandle,
        data: bundleMeta ? { bundleMeta } : undefined,
        label: shouldShowLabel ? edge.label ?? formatRelationType(edge.relationType) : undefined,
        labelStyle: {
          fill: "#253247",
          fontSize: variant === "modified" ? 10 : 11,
          fontWeight: 600,
        },
        labelShowBg: false,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: stroke,
          width: variant === "modified" ? 18 : 14,
          height: variant === "modified" ? 18 : 14,
        },
        animated: Boolean(edge.isCritical && isConnectedToFocus),
        zIndex: variant === "modified" ? 1 : isConnectedToFocus ? 8 : edge.isCritical ? 5 : 2,
        style: {
          stroke,
          strokeWidth:
            variant === "modified"
              ? isConnectedToFocus
                ? 1.6
                : edge.isCritical
                  ? 1.3
                  : edge.isAggregated
                    ? 1.1
                    : 0.9
              : isConnectedToFocus
                ? 2.4
                : edge.isCritical
                  ? 1.9
                  : 1.4,
          opacity:
            hasTypeHighlight
              ? isTypeMatched
                ? variant === "modified"
                  ? 0.86
                  : 0.88
                : variant === "modified"
                  ? 0.14
                  : 0.08
              : variant === "modified"
                ? isConnectedToFocus
                  ? 0.98
                  : isContextEdge
                    ? edge.isCritical
                      ? 0.88
                      : edge.isAggregated
                        ? 0.8
                        : 0.72
                    : 0.38
                : isContextEdge
                  ? 0.88
                  : 0.2,
          strokeDasharray: undefined,
        },
      };
    });
  }, [
    activeEdgeId,
    edges,
    flowNodeLookup,
    focusNodeId,
    forceEdgeLabels,
    hasTypeHighlight,
    highlightSet,
    metrics,
    neighborDepths,
    nodeTypeById,
    selectedNodeId,
    variant,
  ]);

  flowNodesRef.current = flowNodes;

  const syncZoom = () => {
    if (!instance || !onZoomChange) {
      return;
    }

    onZoomChange(Math.round(instance.getZoom() * 100));
  };

  const scheduleSyncZoom = (delay: number, callback: () => void = syncZoom) => {
    const timeoutHandle = window.setTimeout(() => {
      timeoutHandlesRef.current = timeoutHandlesRef.current.filter((handle) => handle !== timeoutHandle);
      callback();
    }, delay);

    timeoutHandlesRef.current.push(timeoutHandle);
  };

  useEffect(() => {
    const host = canvasHostRef.current;

    if (!host || !instance || typeof ResizeObserver === "undefined") {
      return undefined;
    }

    let frame = 0;
    const resizeObserver = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width <= 0 || height <= 0) {
        return;
      }

      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        instance.fitView({ duration: 0, padding: DEFAULT_FIT_PADDING });
        onZoomChange?.(Math.round(instance.getZoom() * 100));
      });
    });

    resizeObserver.observe(host);

    return () => {
      window.cancelAnimationFrame(frame);
      resizeObserver.disconnect();
    };
  }, [flowEdges.length, flowNodes.length, instance, layoutPreset, onZoomChange, variant]);

  useEffect(() => {
    return () => {
      timeoutHandlesRef.current.forEach((timeoutHandle) => {
        window.clearTimeout(timeoutHandle);
      });
      timeoutHandlesRef.current = [];
    };
  }, []);

  useEffect(() => {
    if (!instance) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      instance.fitView({ duration: 220, padding: DEFAULT_FIT_PADDING });
      scheduleSyncZoom(240);
    });

    return () => window.cancelAnimationFrame(frame);
  }, [instance, layoutPreset, nodeKey, variant]);

  useImperativeHandle(
    ref,
    () => ({
      fitView: () => {
        instance?.fitView({ duration: 220, padding: DEFAULT_FIT_PADDING });
        scheduleSyncZoom(220);
      },
      zoomIn: () => {
        if (!instance) {
          return;
        }

        instance.zoomIn({ duration: 180 });
        scheduleSyncZoom(180);
      },
      zoomOut: () => {
        if (!instance) {
          return;
        }

        instance.zoomOut({ duration: 180 });
        scheduleSyncZoom(180);
      },
      recenter: (nodeId) => {
        if (!instance) {
          return;
        }

        if (!nodeId) {
          instance.fitView({ duration: 220, padding: DEFAULT_FIT_PADDING });
          scheduleSyncZoom(220);
          return;
        }

        const node = flowNodesRef.current.find((candidate) => candidate.id === nodeId);
        if (!node) {
          instance.fitView({ duration: 220, padding: DEFAULT_FIT_PADDING });
          scheduleSyncZoom(220);
          return;
        }

        instance.setCenter(node.position.x + metrics.nodeWidth / 2, node.position.y + metrics.nodeHeight / 2, {
          zoom: variant === "modified" ? 1.04 : 1.1,
          duration: 220,
        });
        scheduleSyncZoom(220);
      },
      focusNode: (nodeId) => {
        if (!instance) {
          return;
        }

        const node = flowNodesRef.current.find((candidate) => candidate.id === nodeId);
        if (!node) {
          return;
        }

        instance.fitBounds(
          {
            x: node.position.x - metrics.nodeWidth,
            y: node.position.y - metrics.nodeHeight,
            width: metrics.nodeWidth * 3,
            height: metrics.nodeHeight * 3,
          },
          {
            padding: FOCUSED_FIT_PADDING,
            duration: 240,
          },
        );
        scheduleSyncZoom(240);
      },
    }),
    [instance, metrics, onZoomChange, variant],
  );

  return (
    <div
      className="topology-modified-canvas"
      data-testid="topology-canvas"
      data-topology-canvas-variant={variant}
      ref={canvasHostRef}
    >
      <ReactFlow<Node<CanvasNodeData>, Edge<ExplorerFlowEdgeData>>
        edges={flowEdges}
        edgeTypes={edgeTypes}
        fitView
        minZoom={variant === "modified" ? 0.22 : 0.35}
        nodes={[...groupBoxNodes, ...flowNodes]}
        nodeTypes={nodeTypes}
        nodesDraggable={!isJsdomEnvironment}
        nodesFocusable={false}
        onInit={(flowInstance) => {
          setInstance(flowInstance);
          if (onZoomChange) {
            scheduleSyncZoom(0, () => onZoomChange(Math.round(flowInstance.getZoom() * 100)));
          }
        }}
        onMoveEnd={syncZoom}
        onMoveStart={() => onCanvasInteraction?.()}
        onNodeDragStop={(_, node) => {
          positionsRef.current[node.id] = node.position;
        }}
        onNodeMouseEnter={(event, node) => {
          if (node.type !== "assetNode") {
            return;
          }
          onHoverNode(node.id);
          const data = node.data as ExplorerFlowNodeData;
          setTooltip({
            x: event.clientX,
            y: event.clientY,
            title: data.node.name,
            summary: getNodeSummary(data.node),
          });
        }}
        onNodeMouseLeave={() => {
          onHoverNode(undefined);
          setTooltip(null);
        }}
        onNodeMouseMove={(event, node) => {
          if (node.type !== "assetNode") {
            return;
          }
          const data = node.data as ExplorerFlowNodeData;
          setTooltip({
            x: event.clientX,
            y: event.clientY,
            title: data.node.name,
            summary: getNodeSummary(data.node),
          });
        }}
        onEdgeClick={(_, edge) => {
          setActiveEdgeId(edge.id);
          onCanvasInteraction?.();
        }}
        onNodeClick={(event, node) => {
          if (node.type !== "assetNode") {
            return;
          }
          onSelectNode(node.id);
          const data = node.data as ExplorerFlowNodeData;
          onOpenNodeActions?.({
            node: data.node,
            clientX: event.clientX,
            clientY: event.clientY,
          });
        }}
        onPaneClick={() => {
          onCanvasInteraction?.();
          setActiveEdgeId(undefined);
          onHoverNode(undefined);
          setTooltip(null);
        }}
        panOnDrag={!isJsdomEnvironment}
        proOptions={{ hideAttribution: true }}
      >
        {shouldRenderBackground ? (
          <Background
            color={variant === "modified" ? "rgba(148, 163, 184, 0.55)" : "rgba(177, 186, 198, 0.78)"}
            gap={variant === "modified" ? 26 : 24}
            size={variant === "modified" ? 1.4 : 1.6}
            variant={BackgroundVariant.Dots}
          />
        ) : null}
      </ReactFlow>

      {tooltip ? (
        <div className="topology-modified-tooltip" style={{ left: tooltip.x + 12, top: tooltip.y + 12 }}>
          <p className="topology-modified-tooltip__title">{tooltip.title}</p>
          <p className="topology-modified-tooltip__copy">{tooltip.summary}</p>
        </div>
      ) : null}
    </div>
  );
});

export default TopologyCanvas;

