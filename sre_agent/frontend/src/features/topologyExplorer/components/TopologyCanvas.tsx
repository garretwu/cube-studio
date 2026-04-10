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

import { AppIcon } from "../../../components/ui";

import {

  formatRelationType,

  formatTopologyType,

  getTopologyTypeIconName,

} from "../formatters";

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

  onSelectNode: (nodeId: string) => void;

};



const NODE_WIDTH = 148;

const NODE_HEIGHT = 112;

const NODE_CIRCLE_SIZE = 68;

const DEFAULT_FIT_PADDING = 0.12;

const FOCUSED_FIT_PADDING = 0.1;

const HANDLE_STYLE = {

  top: NODE_CIRCLE_SIZE / 2 + 2,

  width: 10,

  height: 10,

  opacity: 0,

  background: "transparent",

  border: "none",

} as const;



function getEdgeColor(edge: TopologyRelation) {

  if (edge.status === "abnormal") {

    return "#c85656";

  }



  if (edge.status === "impacted") {

    return "#b88230";

  }



  return edge.isAggregated ? "#d9dde4" : "#c4cad6";

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

    cluster: { x: 112, y: 156 },

    rack: { x: 112, y: 388 },

    switch: { x: 452, y: 156 },

    node: { x: 452, y: 396 },

    gpu: { x: 820, y: 332 },

    service: { x: 1160, y: 218 },

  };

  const layerXOffset = 124;

  const layerYOffset = 138;

  const layerXSpacing = 286;

  const layerYSpacing = 156;

  const typeXOffset = 28;

  const typeYOffset = 18;

  const typeYSpacing = 154;



  nodes.forEach((node) => {

    if (layoutPreset === "layered") {

      const columnIndex = layerOrder[node.layer];

      const nextIndex = perLayer.get(node.layer) ?? 0;

      positions.set(node.id, {

        x: layerXOffset + columnIndex * layerXSpacing,

        y: layerYOffset + nextIndex * layerYSpacing,

      });

      perLayer.set(node.layer, nextIndex + 1);

      return;

    }



    const anchor = typeOrder[node.type];

    const nextIndex = perType.get(node.type) ?? 0;

    positions.set(node.id, {

      x: anchor.x + typeXOffset,

      y: anchor.y + typeYOffset + nextIndex * typeYSpacing,

    });

    perType.set(node.type, nextIndex + 1);

  });



  return positions;

}



function ExplorerNode({ data }: NodeProps<Node<ExplorerFlowNodeData>>) {

  const { node, selected, searchHit, dimmed, neighborDepth, onSelectNode } = data;

  const typeLabel = formatTopologyType(node.type);

  const statusLabel =
    node.status === "healthy"
      ? "healthy"
      : node.status === "impacted"
        ? "impacted"
        : node.status === "abnormal"
          ? "abnormal"
          : "unknown";
  const neighborLabel = neighborDepth > 0 ? `, ${neighborDepth}-hop neighbor` : "";



  return (

    <div

      className={`topology-flow-node topology-flow-node--${node.type} topology-flow-node--${node.status} ${selected ? "topology-flow-node--selected" : ""} ${searchHit ? "topology-flow-node--search-hit" : ""} ${dimmed ? "topology-flow-node--dimmed" : ""}`}

      title={`${node.name} | ${node.summary}`}

    >

      <Handle position={Position.Left} style={HANDLE_STYLE} type="target" />

      <button

        aria-label={`${node.name} | ${typeLabel} | ${statusLabel}${neighborLabel}`}

        className="topology-flow-node__button"

        onClick={() => onSelectNode(node.id)}

        type="button"

      >

        <span className="topology-flow-node__orb" aria-hidden="true">

          <span className={`topology-flow-node__icon topology-flow-node__icon--${node.type}`}>

            <AppIcon name={getTopologyTypeIconName(node.type)} size={22} />

          </span>

        </span>

        <span className="topology-flow-node__title">{node.name}</span>

      </button>

      <Handle position={Position.Right} style={HANDLE_STYLE} type="source" />

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

    onSelectNode,

    onHoverNode,

    onZoomChange,

    onOpenNodeActions,

    onCanvasInteraction,

  },

  ref,

) {

  const [instance, setInstance] = useState<ReactFlowInstance<Node<ExplorerFlowNodeData>, Edge> | null>(null);

  const positionsRef = useRef<Record<string, { x: number; y: number }>>({});

  const flowNodesRef = useRef<Node<ExplorerFlowNodeData>[]>([]);

  const timeoutHandlesRef = useRef<number[]>([]);

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



  const scheduleSyncZoom = (delay: number, callback: () => void = syncZoom) => {

    const timeoutHandle = window.setTimeout(() => {

      timeoutHandlesRef.current = timeoutHandlesRef.current.filter((handle) => handle !== timeoutHandle);

      callback();

    }, delay);



    timeoutHandlesRef.current.push(timeoutHandle);

  };



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

  }, [instance, layoutPreset, nodes.length]);



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



        instance.setCenter(node.position.x + NODE_WIDTH / 2, node.position.y + NODE_HEIGHT / 2, {

          zoom: 1.1,

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

            x: node.position.x - 160,

            y: node.position.y - 136,

            width: NODE_WIDTH + 320,

            height: NODE_HEIGHT + 272,

          },

          {

            padding: FOCUSED_FIT_PADDING,

            duration: 240,

          },

        );

        scheduleSyncZoom(240);

      },

    }),

    [instance, onZoomChange],

  );



  return (

    <div className="topology-modified-canvas" data-testid="topology-canvas">

      <ReactFlow

        edges={flowEdges}

        fitView

        minZoom={0.35}

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
          <Background color="rgba(177, 186, 198, 0.78)" gap={24} size={1.6} variant={BackgroundVariant.Dots} />
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

