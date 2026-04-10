import {
  Background,
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
import { AppIcon } from "../../../components/ui";
import {
  formatRelationType,
  formatTopologyStatus,
  formatTopologyType,
  getNodeMetricSummary,
  getTopologyTypeIconName,
} from "../formatters";
import type {
  ExplorerLayerFilter,
  ExplorerLayoutPreset,
  ExplorerStatusFilter,
  ExplorerSummaryFilter,
  TopologyCanvasExport,
} from "../types";

type TopologyCanvasProps = {
  nodes: TopologyObject[];
  edges: TopologyRelation[];
  selectedNodeId?: string;
  hoveredNodeId?: string;
  forceEdgeLabels?: boolean;
  matchedNodeIds: string[];
  neighborDepths: Map<string, number>;
  layoutPreset: ExplorerLayoutPreset;
  lastUpdated?: string;
  searchQuery: string;
  statusFilter: ExplorerStatusFilter;
  layerFilter: ExplorerLayerFilter;
  summaryFilter: ExplorerSummaryFilter;
  onSelectNode: (nodeId: string) => void;
  onHoverNode: (nodeId?: string) => void;
  onZoomChange?: (zoomPercent: number) => void;
  onReadyStateChange?: (ready: boolean) => void;
};

export type TopologyCanvasHandle = {
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  recenter: (nodeId?: string) => void;
  focusNode: (nodeId: string) => void;
  exportView: () => TopologyCanvasExport | null;
};

type ExplorerFlowNodeData = {
  node: TopologyObject;
  selected: boolean;
  searchHit: boolean;
  dimmed: boolean;
  neighborDepth: number;
  onSelectNode: (nodeId: string) => void;
};

const NODE_WIDTH = 216;
const NODE_HEIGHT = 108;

function getEdgeColor(edge: TopologyRelation) {
  if (edge.status === "abnormal") {
    return "#dc2626";
  }

  if (edge.status === "impacted") {
    return "#d97706";
  }

  return edge.isAggregated ? "#64748b" : "#2b5876";
}

function getBasePositions(nodes: TopologyObject[], layoutPreset: ExplorerLayoutPreset) {
  const positions = new Map<string, { x: number; y: number }>();
  const perLayer = new Map<string, number>();
  const perType = new Map<string, number>();
  const layerOrder: Record<TopologyObject["layer"], number> = {
    physical: 0,
    network: 1,
    compute: 2,
    service: 3,
  };
  const typeOrder: Record<TopologyObject["type"], { x: number; y: number }> = {
    cluster: { x: 120, y: 120 },
    rack: { x: 120, y: 350 },
    switch: { x: 430, y: 120 },
    node: { x: 430, y: 320 },
    gpu: { x: 760, y: 220 },
    pod: { x: 1020, y: 220 },
    service: { x: 1030, y: 340 },
  };

  nodes.forEach((node) => {
    if (layoutPreset === "layered") {
      const columnIndex = layerOrder[node.layer];
      const nextIndex = perLayer.get(node.layer) ?? 0;
      positions.set(node.id, {
        x: 132 + columnIndex * 252,
        y: 104 + nextIndex * 128,
      });
      perLayer.set(node.layer, nextIndex + 1);
      return;
    }

    const anchor = typeOrder[node.type];
    const nextIndex = perType.get(node.type) ?? 0;
    positions.set(node.id, {
      x: anchor.x + 42,
      y: anchor.y + 26 + nextIndex * 128,
    });
    perType.set(node.type, nextIndex + 1);
  });

  return positions;
}

function ExplorerNode({ data }: NodeProps<Node<ExplorerFlowNodeData>>) {
  const { node, selected, searchHit, dimmed, neighborDepth, onSelectNode } = data;
  const neighborLabel = neighborDepth > 0 ? ` · ${neighborDepth} 跳邻居` : "";

  return (
    <div
      className={`topology-flow-node topology-flow-node--${node.type} topology-flow-node--${node.status} ${selected ? "topology-flow-node--selected" : ""} ${searchHit ? "topology-flow-node--search-hit" : ""} ${dimmed ? "topology-flow-node--dimmed" : ""}`}
      title={`${node.name} · ${node.summary}`}
    >
      <Handle position={Position.Left} type="target" />
      <button className="topology-flow-node__button" onClick={() => onSelectNode(node.id)} type="button">
        <div className="topology-flow-node__header">
          <span className={`topology-flow-node__icon topology-flow-node__icon--${node.type}`} aria-hidden="true">
            <AppIcon name={getTopologyTypeIconName(node.type)} size={16} />
          </span>
          <div className="topology-flow-node__heading">
            <span className="topology-flow-node__eyebrow">
              {formatTopologyType(node.type)}
              {neighborLabel}
            </span>
            <strong className="topology-flow-node__title">{node.name}</strong>
          </div>
        </div>
        <span className="topology-flow-node__status">{formatTopologyStatus(node.status)}</span>
        <span className="topology-flow-node__summary">{getNodeMetricSummary(node)}</span>
      </button>
      <Handle position={Position.Right} type="source" />
    </div>
  );
}

const nodeTypes = {
  assetNode: ExplorerNode,
};

const shouldRenderBackground = typeof navigator === "undefined" || !/jsdom/i.test(navigator.userAgent);

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
    lastUpdated,
    searchQuery,
    statusFilter,
    layerFilter,
    summaryFilter,
    onSelectNode,
    onHoverNode,
    onZoomChange,
    onReadyStateChange,
  },
  ref,
) {
  const [instance, setInstance] = useState<ReactFlowInstance<Node<ExplorerFlowNodeData>, Edge> | null>(null);
  const positionsRef = useRef<Record<string, { x: number; y: number }>>({});
  const flowNodesRef = useRef<Node<ExplorerFlowNodeData>[]>([]);
  const layoutRef = useRef(layoutPreset);
  const nodeKeyRef = useRef(nodes.map((node) => node.id).join("|"));
  const [tooltip, setTooltip] = useState<{ x: number; y: number; title: string; summary: string } | null>(null);
  const focusNodeId = selectedNodeId ?? hoveredNodeId;

  if (layoutRef.current !== layoutPreset || nodeKeyRef.current !== nodes.map((node) => node.id).join("|")) {
    positionsRef.current = {};
    layoutRef.current = layoutPreset;
    nodeKeyRef.current = nodes.map((node) => node.id).join("|");
  }

  const flowNodes = useMemo<Node<ExplorerFlowNodeData>[]>(() => {
    const layoutPositions = getBasePositions(nodes, layoutPreset);
    return nodes.map((node) => {
      const position = positionsRef.current[node.id] ?? layoutPositions.get(node.id) ?? { x: 0, y: 0 };
      const neighborDepth = neighborDepths.get(node.id) ?? -1;
      const dimmed = Boolean(selectedNodeId) && node.id !== selectedNodeId && neighborDepth < 1;

      return {
        id: node.id,
        type: "assetNode",
        position,
        draggable: true,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        data: {
          node,
          selected: node.id === selectedNodeId,
          searchHit: matchedNodeIds.includes(node.id),
          dimmed,
          neighborDepth,
          onSelectNode,
        },
      };
    });
  }, [layoutPreset, matchedNodeIds, neighborDepths, nodes, onSelectNode, selectedNodeId]);

  const flowEdges = useMemo<Edge[]>(() => {
    return edges.map((edge) => {
      const isConnectedToFocus = Boolean(focusNodeId) && (edge.source === focusNodeId || edge.target === focusNodeId);
      const isContextEdge =
        !focusNodeId ||
        edge.source === focusNodeId ||
        edge.target === focusNodeId ||
        (neighborDepths.has(edge.source) && neighborDepths.has(edge.target));

      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: forceEdgeLabels || isConnectedToFocus ? edge.label ?? formatRelationType(edge.relationType) : undefined,
        labelStyle: {
          fill: "#102038",
          fontSize: 11,
          fontWeight: 600,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: getEdgeColor(edge),
        },
        animated: edge.isCritical && isContextEdge,
        style: {
          stroke: getEdgeColor(edge),
          strokeWidth: isConnectedToFocus ? 2.4 : edge.isCritical ? 1.9 : 1.4,
          opacity: isContextEdge ? 0.88 : 0.2,
          strokeDasharray: edge.isAggregated ? "8 6" : undefined,
        },
      };
    });
  }, [edges, focusNodeId, forceEdgeLabels, neighborDepths]);

  flowNodesRef.current = flowNodes;

  const syncZoom = () => {
    if (!instance || !onZoomChange) {
      return;
    }

    onZoomChange(Math.round(instance.getZoom() * 100));
  };

  useEffect(() => {
    onReadyStateChange?.(Boolean(instance && nodes.length > 0));

    return () => {
      onReadyStateChange?.(false);
    };
  }, [instance, nodes.length, onReadyStateChange]);

  useEffect(() => {
    if (!instance) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      instance.fitView({ duration: 220, padding: 0.2 });
      window.setTimeout(syncZoom, 240);
    });

    return () => window.cancelAnimationFrame(frame);
  }, [instance, layoutPreset, nodes.length]);

  useImperativeHandle(
    ref,
    () => ({
      fitView: () => {
        instance?.fitView({ duration: 220, padding: 0.18 });
        window.setTimeout(syncZoom, 220);
      },
      zoomIn: () => {
        if (!instance) {
          return;
        }

        instance.zoomIn({ duration: 180 });
        window.setTimeout(syncZoom, 180);
      },
      zoomOut: () => {
        if (!instance) {
          return;
        }

        instance.zoomOut({ duration: 180 });
        window.setTimeout(syncZoom, 180);
      },
      recenter: (nodeId) => {
        if (!instance) {
          return;
        }

        if (!nodeId) {
          instance.fitView({ duration: 220, padding: 0.18 });
          window.setTimeout(syncZoom, 220);
          return;
        }

        const node = flowNodesRef.current.find((candidate) => candidate.id === nodeId);
        if (!node) {
          instance.fitView({ duration: 220, padding: 0.18 });
          window.setTimeout(syncZoom, 220);
          return;
        }

        instance.setCenter(node.position.x + NODE_WIDTH / 2, node.position.y + NODE_HEIGHT / 2, {
          zoom: 1.1,
          duration: 220,
        });
        window.setTimeout(syncZoom, 220);
      },
      focusNode: (nodeId) => {
        if (!instance) {
          return;
        }

        const node = flowNodesRef.current.find((candidate) => candidate.id === nodeId);
        if (!node) {
          return;
        }

        instance.setCenter(node.position.x + NODE_WIDTH / 2, node.position.y + NODE_HEIGHT / 2, {
          zoom: 1.2,
          duration: 240,
        });
        window.setTimeout(syncZoom, 240);
      },
      exportView: () => {
        if (!instance || flowNodesRef.current.length === 0) {
          return null;
        }

        const instanceNodes = instance.getNodes();
        if (instanceNodes.length === 0) {
          return null;
        }

        const instanceNodeMap = new Map(instanceNodes.map((node) => [node.id, node]));
        const fallbackNodeMap = new Map(flowNodesRef.current.map((node) => [node.id, node]));

        return {
          meta: {
            exportedAt: new Date().toISOString(),
            source: "topology-modified-canvas",
            lastUpdated,
            viewMode: "graph",
            layoutPreset,
            zoomPercent: Math.round(instance.getZoom() * 100),
          },
          filters: {
            statusFilter,
            layerFilter,
            summaryFilter,
            searchQuery,
          },
          focus: {
            selectedNodeId,
            hoveredNodeId,
            matchedNodeIds,
          },
          nodes: nodes.map((node) => {
            const instanceNode = instanceNodeMap.get(node.id);
            const fallbackNode = fallbackNodeMap.get(node.id);
            const position = positionsRef.current[node.id] ?? instanceNode?.position ?? fallbackNode?.position ?? { x: 0, y: 0 };

            return {
              id: node.id,
              name: node.name,
              type: node.type,
              status: node.status,
              layer: node.layer,
              domain: node.domain,
              region: node.region,
              zone: node.zone,
              cluster: node.cluster,
              rack: node.rack,
              slot: node.slot,
              summary: node.summary,
              tags: node.tags,
              metrics: node.metrics,
              attributes: node.attributes,
              position,
              size: {
                width: instanceNode?.width ?? fallbackNode?.width ?? NODE_WIDTH,
                height: instanceNode?.height ?? fallbackNode?.height ?? NODE_HEIGHT,
              },
            };
          }),
          edges: edges.map((edge) => ({
            id: edge.id,
            source: edge.source,
            target: edge.target,
            relationType: edge.relationType,
            status: edge.status,
            impactLevel: edge.impactLevel,
            label: edge.label,
            isCritical: edge.isCritical,
            isAggregated: edge.isAggregated,
          })),
        };
      },
    }),
    [
      edges,
      hoveredNodeId,
      instance,
      lastUpdated,
      layerFilter,
      layoutPreset,
      matchedNodeIds,
      nodes,
      onZoomChange,
      searchQuery,
      selectedNodeId,
      statusFilter,
      summaryFilter,
    ],
  );

  return (
    <div className="topology-modified-canvas" data-testid="topology-canvas">
      <ReactFlow
        edges={flowEdges}
        fitView
        minZoom={0.35}
        nodes={flowNodes}
        nodeTypes={nodeTypes}
        nodesDraggable
        nodesFocusable={false}
        onInit={(flowInstance) => {
          setInstance(flowInstance);
          if (onZoomChange) {
            window.setTimeout(() => onZoomChange(Math.round(flowInstance.getZoom() * 100)), 0);
          }
        }}
        onMoveEnd={syncZoom}
        onNodeDragStop={(_, node) => {
          positionsRef.current[node.id] = node.position;
        }}
        onNodeMouseEnter={(event, node) => {
          onHoverNode(node.id);
          setTooltip({
            x: event.clientX,
            y: event.clientY,
            title: node.data.node.name,
            summary: node.data.node.summary,
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
            summary: node.data.node.summary,
          });
        }}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        panOnDrag
        proOptions={{ hideAttribution: true }}
      >
        {shouldRenderBackground ? <Background color="rgba(148, 163, 184, 0.22)" gap={18} /> : null}
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
