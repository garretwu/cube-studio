import { render, waitFor } from "@testing-library/react";
import { createRef, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { topologyExplorerMock } from "../../../mocks/topologyExplorerData";
import TopologyCanvas, { type TopologyCanvasHandle } from "./TopologyCanvas";

const reactFlowMock = vi.hoisted(() => {
  let latestProps: Record<string, unknown> | null = null;
  let instanceNodes: Array<{ id: string; position: { x: number; y: number }; width?: number; height?: number }> = [];
  let shouldInit = true;

  const instance = {
    fitView: vi.fn(),
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    setCenter: vi.fn(),
    getZoom: vi.fn(() => 1.1),
    getNodes: vi.fn(() => instanceNodes),
  };

  return {
    instance,
    setLatestProps: (props: Record<string, unknown>) => {
      latestProps = props;
    },
    getLatestProps: () => latestProps,
    setInstanceNodes: (nodes: Array<{ id: string; position: { x: number; y: number }; width?: number; height?: number }>) => {
      instanceNodes = nodes;
    },
    setShouldInit: (value: boolean) => {
      shouldInit = value;
    },
    shouldInit: () => shouldInit,
    reset: () => {
      latestProps = null;
      instanceNodes = [];
      shouldInit = true;
      instance.fitView.mockClear();
      instance.zoomIn.mockClear();
      instance.zoomOut.mockClear();
      instance.setCenter.mockClear();
      instance.getZoom.mockClear();
      instance.getZoom.mockReturnValue(1.1);
      instance.getNodes.mockClear();
      instance.getNodes.mockImplementation(() => instanceNodes);
    },
  };
});

vi.mock("@xyflow/react", async () => {
  const React = await import("react");

  return {
    Background: () => null,
    Handle: () => null,
    MarkerType: { ArrowClosed: "arrow-closed" },
    Position: { Left: "left", Right: "right" },
    ReactFlow: (props: Record<string, unknown>) => {
      reactFlowMock.setLatestProps(props);

      React.useEffect(() => {
        if (reactFlowMock.shouldInit()) {
          (props.onInit as ((instance: typeof reactFlowMock.instance) => void) | undefined)?.(reactFlowMock.instance);
        }
      }, [props]);

      return React.createElement("div", { "data-testid": "mock-react-flow" }, props.children as ReactNode);
    },
  };
});

const sampleNodes = topologyExplorerMock.nodes.slice(0, 3);
const sampleEdges = topologyExplorerMock.edges.filter(
  (edge) => sampleNodes.some((node) => node.id === edge.source) && sampleNodes.some((node) => node.id === edge.target),
);

function renderCanvas(ref = createRef<TopologyCanvasHandle>()) {
  render(
    <TopologyCanvas
      ref={ref}
      edges={sampleEdges}
      hoveredNodeId={sampleNodes[1]?.id}
      lastUpdated={topologyExplorerMock.lastUpdated}
      layerFilter="all"
      layoutPreset="layered"
      matchedNodeIds={[sampleNodes[2]?.id ?? ""]}
      neighborDepths={new Map([[sampleNodes[0]?.id ?? "", 0]])}
      nodes={sampleNodes}
      onHoverNode={() => undefined}
      onReadyStateChange={() => undefined}
      onSelectNode={() => undefined}
      searchQuery="svc"
      selectedNodeId={sampleNodes[0]?.id}
      statusFilter="abnormal"
      summaryFilter="impacted"
    />,
  );

  return ref;
}

describe("TopologyCanvas", () => {
  beforeEach(() => {
    reactFlowMock.reset();
  });

  it("exports the current graph view with positions, filters, and metadata", async () => {
    reactFlowMock.setInstanceNodes(
      sampleNodes.map((node, index) => ({
        id: node.id,
        position: { x: 100 + index * 80, y: 160 + index * 48 },
        width: 240,
        height: 120,
      })),
    );

    const ref = renderCanvas();

    await waitFor(() => {
      expect(ref.current).not.toBeNull();
    });

    const payload = ref.current?.exportView();
    expect(payload).not.toBeNull();
    expect(payload?.meta.source).toBe("topology-modified-canvas");
    expect(payload?.meta.viewMode).toBe("graph");
    expect(payload?.meta.layoutPreset).toBe("layered");
    expect(payload?.meta.zoomPercent).toBe(110);
    expect(payload?.filters).toEqual({
      statusFilter: "abnormal",
      layerFilter: "all",
      summaryFilter: "impacted",
      searchQuery: "svc",
    });
    expect(payload?.focus.selectedNodeId).toBe(sampleNodes[0]?.id);
    expect(payload?.focus.hoveredNodeId).toBe(sampleNodes[1]?.id);
    expect(payload?.focus.matchedNodeIds).toEqual([sampleNodes[2]?.id]);
    expect(payload?.nodes[0]).toEqual(
      expect.objectContaining({
        id: sampleNodes[0]?.id,
        position: { x: 100, y: 160 },
        size: { width: 240, height: 120 },
      }),
    );
    expect(payload?.edges[0]).toEqual(
      expect.objectContaining({
        id: sampleEdges[0]?.id,
        source: sampleEdges[0]?.source,
        target: sampleEdges[0]?.target,
      }),
    );
  });

  it("prefers dragged node positions when exporting", async () => {
    reactFlowMock.setInstanceNodes(
      sampleNodes.map((node, index) => ({
        id: node.id,
        position: { x: 20 + index * 50, y: 30 + index * 40 },
        width: 216,
        height: 108,
      })),
    );

    const ref = renderCanvas();

    await waitFor(() => {
      expect(ref.current).not.toBeNull();
    });

    const latestProps = reactFlowMock.getLatestProps() as {
      onNodeDragStop?: (event: unknown, node: { id: string; position: { x: number; y: number } }) => void;
    } | null;

    latestProps?.onNodeDragStop?.({}, { id: sampleNodes[0]!.id, position: { x: 720, y: 460 } });

    const payload = ref.current?.exportView();
    const draggedNode = payload?.nodes.find((node) => node.id === sampleNodes[0]?.id);
    expect(draggedNode?.position).toEqual({ x: 720, y: 460 });
  });

  it("returns null when the canvas instance is unavailable or there are no nodes", async () => {
    reactFlowMock.setShouldInit(false);

    const ref = renderCanvas();

    await waitFor(() => {
      expect(ref.current).not.toBeNull();
    });
    expect(ref.current?.exportView()).toBeNull();

    reactFlowMock.reset();
    reactFlowMock.setInstanceNodes([]);
    const emptyRef = createRef<TopologyCanvasHandle>();
    render(
      <TopologyCanvas
        ref={emptyRef}
        edges={[]}
        lastUpdated={topologyExplorerMock.lastUpdated}
        layerFilter="all"
        layoutPreset="layered"
        matchedNodeIds={[]}
        neighborDepths={new Map()}
        nodes={[]}
        onHoverNode={() => undefined}
        onReadyStateChange={() => undefined}
        onSelectNode={() => undefined}
        searchQuery=""
        statusFilter="all"
        summaryFilter="all"
      />,
    );

    await waitFor(() => {
      expect(emptyRef.current).not.toBeNull();
    });
    expect(emptyRef.current?.exportView()).toBeNull();
  });
});
