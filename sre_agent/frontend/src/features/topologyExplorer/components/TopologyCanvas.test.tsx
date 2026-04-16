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
    fitBounds: vi.fn(),
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
      instance.fitBounds.mockClear();
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
    BackgroundVariant: { Dots: "dots" },
    BaseEdge: () => null,
    Handle: () => null,
    MarkerType: { ArrowClosed: "arrow-closed" },
    Position: { Left: "left", Right: "right", Top: "top", Bottom: "bottom" },
    getBezierPath: () => ["M 0,0 C 1,1 2,2 3,3", 0, 0],
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
      layoutPreset="layered"
      matchedNodeIds={[sampleNodes[2]?.id ?? ""]}
      neighborDepths={new Map([[sampleNodes[0]?.id ?? "", 0]])}
      nodes={sampleNodes}
      onHoverNode={() => undefined}
      onSelectNode={() => undefined}
      selectedNodeId={sampleNodes[0]?.id}
    />,
  );

  return ref;
}

describe("TopologyCanvas", () => {
  beforeEach(() => {
    reactFlowMock.reset();
  });

  it("exposes imperative handle methods and proxies to ReactFlow instance", async () => {
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

    ref.current?.fitView();
    ref.current?.zoomIn();
    ref.current?.zoomOut();
    ref.current?.recenter(sampleNodes[0]?.id);
    ref.current?.focusNode(sampleNodes[1]?.id ?? "");

    expect(reactFlowMock.instance.fitView).toHaveBeenCalled();
    expect(reactFlowMock.instance.zoomIn).toHaveBeenCalled();
    expect(reactFlowMock.instance.zoomOut).toHaveBeenCalled();
    expect(reactFlowMock.instance.setCenter).toHaveBeenCalled();
  });

  it("keeps handle available when onInit is not fired", async () => {
    reactFlowMock.setShouldInit(false);
    const ref = renderCanvas();

    await waitFor(() => {
      expect(ref.current).not.toBeNull();
    });

    expect(() => ref.current?.fitView()).not.toThrow();
    expect(() => ref.current?.recenter()).not.toThrow();
  });
});
