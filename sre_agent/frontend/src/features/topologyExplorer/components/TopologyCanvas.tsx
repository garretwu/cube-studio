import {
  Background,
  BackgroundVariant,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";

import type { TopologyObject, TopologyRelation } from "../../../api/types";
import {
  formatRelationType,
  formatTopologyStatus,
  formatTopologyType,
  getTopologyTypeIconAsset,
} from "../formatters";
import { isSyntheticServiceAggregateNode } from "../selectors";
import {
  DEFAULT_FIT_PADDING,
  FOCUSED_FIT_PADDING,
  TOPOLOGY_CANVAS_METRICS,
  type TopologyCanvasMetrics,
  type TopologyCanvasVariant,
} from "../canvasConfig";
import { computeModifiedHybridLayout } from "../modifiedLayout";
import {
  deriveModifiedEdgeRouting,
  getModifiedHandlePosition,
  MODIFIED_EDGE_HANDLE_IDS,
} from "../modifiedEdgeRouting";
import type { ExplorerLayoutPreset } from "../types";

type TopologyCanvasProps = {
  nodes: TopologyObject[];
  edges: TopologyRelation[];
  selectedNodeId?: string;
  hoveredNodeId?: string;
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
  neighborDepth: number;
  metrics: TopologyCanvasMetrics;
  variant: TopologyCanvasVariant;
  onSelectNode: (nodeId: string) => void;
};

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
  return rawType === "pod" ? "Pod" : "服务";
}

function getNodeSummary(node: TopologyObject) {
  if (isSyntheticServiceAggregateNode(node)) {
    return `聚合 ${getAggregateCount(node)} 个${getAggregateTypeLabel(node)}对象，点击展开查看。`;
  }

  return node.summary;
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
  const orbCenterY = metrics.nodeCircleSize / 2 + 2;

  if (position === Position.Left) {
    return {
      ...HANDLE_STYLE,
      left: -2,
      top: orbCenterY,
      transform: "translateY(-50%)",
    };
  }

  if (position === Position.Right) {
    return {
      ...HANDLE_STYLE,
      right: -2,
      top: orbCenterY,
      transform: "translateY(-50%)",
    };
  }

  if (position === Position.Top) {
    return {
      ...HANDLE_STYLE,
      left: metrics.nodeWidth / 2,
      top: 4,
      transform: "translateX(-50%)",
    };
  }

  return {
    ...HANDLE_STYLE,
    left: metrics.nodeWidth / 2,
    top: metrics.nodeCircleSize + 12,
    transform: "translateX(-50%)",
  };
}

function ExplorerNode({ data }: NodeProps<Node<ExplorerFlowNodeData>>) {
  const { node, selected, searchHit, dimmed, neighborDepth, metrics, variant, onSelectNode } = data;
  const isAggregate = isSyntheticServiceAggregateNode(node);
  const aggregateCount = getAggregateCount(node);
  const handleStyle = { ...HANDLE_STYLE, top: metrics.nodeCircleSize / 2 + 2 };
  const statusLabel = formatTopologyStatus(node.status);
  const semanticTypeLabel = isAggregate ? `${getAggregateTypeLabel(node)}聚合组` : formatTopologyType(node.type);
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
              style={{ width: metrics.iconSize + 2, height: metrics.iconSize + 2 }}
            />
            {isAggregate ? <span className="topology-flow-node__count-badge">{aggregateCount}</span> : null}
          </span>
          <span className="topology-flow-node__title">{node.name}</span>
          {isAggregate ? <span className="topology-flow-node__meta">{aggregateCount} 个对象</span> : null}
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
};

const isJsdomEnvironment = typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);
const shouldRenderBackground = typeof navigator === "undefined" || !isJsdomEnvironment;

const TopologyCanvas = forwardRef<TopologyCanvasHandle, TopologyCanvasProps>(function TopologyCanvas(
  {
    nodes,
    edges,
    selectedNodeId,
    hoveredNodeId,
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
  const [instance, setInstance] = useState<ReactFlowInstance<Node<ExplorerFlowNodeData>, Edge> | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; title: string; summary: string } | null>(null);
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
      const dimmed = Boolean(selectedNodeId) && node.id !== selectedNodeId && neighborDepth < 1;

      return {
        id: node.id,
        type: "assetNode",
        position,
        draggable: true,
        width: metrics.nodeWidth,
        height: metrics.nodeHeight,
        data: {
          node,
          selected: node.id === selectedNodeId,
          searchHit: matchedNodeIds.includes(node.id),
          dimmed,
          neighborDepth,
          metrics,
          variant,
          onSelectNode,
        },
      };
    });
  }, [layoutPositions, matchedNodeIds, metrics, neighborDepths, nodes, onSelectNode, selectedNodeId, variant]);

  const flowNodeLookup = useMemo(() => new Map(flowNodes.map((node) => [node.id, node])), [flowNodes]);

  const flowEdges = useMemo<Edge[]>(() => {
    return edges.map((edge) => {
      const isConnectedToFocus = Boolean(focusNodeId) && (edge.source === focusNodeId || edge.target === focusNodeId);
      const isContextEdge =
        !focusNodeId ||
        edge.source === focusNodeId ||
        edge.target === focusNodeId ||
        (neighborDepths.has(edge.source) && neighborDepths.has(edge.target));
      const stroke = getEdgeColor(edge, variant);
      const shouldShowLabel = forceEdgeLabels || isConnectedToFocus;
      const routing =
        variant === "modified"
          ? deriveModifiedEdgeRouting({
              sourceNode: flowNodeLookup.get(edge.source),
              targetNode: flowNodeLookup.get(edge.target),
              metrics,
            })
          : undefined;

      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: routing?.edgeType ?? "default",
        sourceHandle: routing?.sourceHandle,
        targetHandle: routing?.targetHandle,
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
        },
        animated: Boolean(edge.isCritical && isConnectedToFocus),
        zIndex: isConnectedToFocus ? 8 : edge.isCritical ? 5 : 2,
        style: {
          stroke,
          strokeWidth:
            variant === "modified"
              ? isConnectedToFocus
                ? 2.4
                : edge.isCritical
                  ? 1.8
                  : edge.isAggregated
                    ? 1.1
                    : 1
              : isConnectedToFocus
                ? 2.4
                : edge.isCritical
                  ? 1.9
                  : 1.4,
          opacity:
            variant === "modified"
              ? isConnectedToFocus
                ? 0.94
                : isContextEdge
                  ? edge.isCritical
                    ? 0.46
                    : edge.isAggregated
                      ? 0.2
                      : 0.16
                  : 0.08
              : isContextEdge
                ? 0.88
                : 0.2,
          strokeDasharray: variant === "modified" && edge.isAggregated ? "4 6" : undefined,
        },
      };
    });
  }, [edges, flowNodeLookup, focusNodeId, forceEdgeLabels, metrics, neighborDepths, variant]);

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
      <ReactFlow
        edges={flowEdges}
        fitView
        minZoom={variant === "modified" ? 0.22 : 0.35}
        nodes={flowNodes}
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
          onHoverNode(node.id);
          setTooltip({
            x: event.clientX,
            y: event.clientY,
            title: node.data.node.name,
            summary: getNodeSummary(node.data.node),
          });
        }}
        onNodeMouseLeave={() => {
          onHoverNode(undefined);
          setTooltip(null);
        }}
        onNodeMouseMove={(event, node) => {
          setTooltip({
            x: event.clientX,
            y: event.clientY,
            title: node.data.node.name,
            summary: getNodeSummary(node.data.node),
          });
        }}
        onNodeClick={(event, node) => {
          onSelectNode(node.id);
          onOpenNodeActions?.({
            node: node.data.node,
            clientX: event.clientX,
            clientY: event.clientY,
          });
        }}
        onPaneClick={() => {
          onCanvasInteraction?.();
          onHoverNode(undefined);
          setTooltip(null);
        }}
        panOnDrag={!isJsdomEnvironment}
        proOptions={{ hideAttribution: true }}
      >
        {shouldRenderBackground ? (
          <Background
            color={variant === "modified" ? "rgba(196, 206, 220, 0.62)" : "rgba(177, 186, 198, 0.78)"}
            gap={variant === "modified" ? 26 : 24}
            size={variant === "modified" ? 1.2 : 1.6}
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


