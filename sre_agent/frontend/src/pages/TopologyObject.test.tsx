import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { apiClient } from "../api/client";
import type { TopologyExplorerResponse } from "../api/types";
import { createTopologyExplorerState, useTopologyExplorerStore } from "../features/topologyExplorer/store";
import TopologyObjectPage from "./TopologyObject";
import TopologyPage from "./Topology";

const topologyCanvasMock = vi.hoisted(() => ({
  latestProps: undefined as Record<string, unknown> | undefined,
  fitView: vi.fn(),
  zoomIn: vi.fn(),
  zoomOut: vi.fn(),
}));

vi.mock("../features/topologyExplorer/components/TopologyCanvas", () => {
  const MockTopologyCanvas = React.forwardRef(function MockTopologyCanvas(props: Record<string, unknown>, ref) {
    topologyCanvasMock.latestProps = props;
    React.useImperativeHandle(ref, () => ({
      fitView: topologyCanvasMock.fitView,
      zoomIn: topologyCanvasMock.zoomIn,
      zoomOut: topologyCanvasMock.zoomOut,
      recenter: vi.fn(),
      focusNode: vi.fn(),
    }));
    return <div data-testid="mock-topology-canvas" />;
  });

  return {
    __esModule: true,
    default: MockTopologyCanvas,
  };
});

vi.mock("../api/client", () => ({
  apiClient: {
    getTopologyExplorer: vi.fn(),
  },
}));

function buildTopologyObjectFixture(): TopologyExplorerResponse {
  const now = "2026-04-17T00:00:00.000Z";
  return {
    site: {
      id: "site-1",
      name: "Fixture Site",
      region: "cn",
      zone: "z1",
      domain: "aidc",
      summary: "fixture",
    },
    nodes: [
      {
        id: "svc:service:demo-agent",
        name: "service/demo-agent",
        type: "service",
        status: "healthy",
        layer: "service",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "service",
        tags: [],
        updatedAt: now,
        attributes: { namespace: "service", cluster_id: "k8s:aidc-lab" },
      },
      {
        id: "ns:service",
        name: "service",
        type: "service",
        status: "healthy",
        layer: "service",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "namespace group",
        tags: [],
        updatedAt: now,
        attributes: { kind: "namespace_group", namespace: "service", cluster_id: "k8s:aidc-lab" },
      },
      {
        id: "wj-lab-ctl-02",
        name: "wj-lab-ctl-02",
        type: "node",
        status: "healthy",
        layer: "compute",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "node",
        tags: [],
        updatedAt: now,
        attributes: { cluster_id: "k8s:aidc-lab" },
      },
      {
        id: "pod:service:demo-agent-0",
        name: "service/demo-agent-0",
        type: "pod",
        status: "healthy",
        layer: "service",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        cluster: "k8s:aidc-lab",
        summary: "pod",
        tags: [],
        updatedAt: now,
        attributes: { namespace: "service" },
      },
      {
        id: "sw-200g:200GE1/0/1",
        name: "200GE1/0/1",
        type: "port",
        status: "healthy",
        layer: "network",
        domain: "aidc",
        region: "cn",
        zone: "z1",
        summary: "port",
        tags: [],
        updatedAt: now,
        attributes: {},
      },
    ],
    edges: [
      {
        id: "e-service-ns",
        source: "svc:service:demo-agent",
        target: "ns:service",
        relationType: "contains",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
        label: "part_of",
      },
      {
        id: "e-service-node",
        source: "svc:service:demo-agent",
        target: "wj-lab-ctl-02",
        relationType: "runs_on",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
        label: "hosted_on",
      },
      {
        id: "e-service-pod",
        source: "svc:service:demo-agent",
        target: "pod:service:demo-agent-0",
        relationType: "depends_on",
        status: "healthy",
        isCritical: false,
        impactLevel: "low",
        label: "serves",
      },
    ],
    paths: [],
    lastUpdated: now,
  };
}

function renderObjectRoute(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/topology/object/:nodeId/*" element={<TopologyObjectPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopologyObjectPage", () => {
  const focalServiceId = "svc:service:demo-agent";
  const encodedFocalServiceId = encodeURIComponent(focalServiceId);

  beforeEach(() => {
    topologyCanvasMock.latestProps = undefined;
    topologyCanvasMock.fitView.mockClear();
    topologyCanvasMock.zoomIn.mockClear();
    topologyCanvasMock.zoomOut.mockClear();
    useTopologyExplorerStore.setState(createTopologyExplorerState());
    vi.mocked(apiClient.getTopologyExplorer).mockReset();
    vi.mocked(apiClient.getTopologyExplorer).mockResolvedValue(buildTopologyObjectFixture());
  });

  it("renders the two-column object topology layout for a focal object", async () => {
    renderObjectRoute(`/topology/object/${encodedFocalServiceId}`);

    await waitFor(() => {
      expect(screen.getByTestId("topology-object-layout")).toBeInTheDocument();
    });

    expect(screen.getByTestId("topology-object-inspector")).toBeInTheDocument();
    expect(screen.getByTestId("mock-topology-canvas")).toBeInTheDocument();
    expect(topologyCanvasMock.latestProps).toEqual(
      expect.objectContaining({
        objectFocusNodeId: focalServiceId,
        variant: "modified",
      }),
    );
    await waitFor(() => {
      expect(topologyCanvasMock.fitView).toHaveBeenCalledWith("balanced");
    });
  });

  it("uses full fit for adapt and balanced fit for reset", async () => {
    const user = userEvent.setup();
    renderObjectRoute(`/topology/object/${encodedFocalServiceId}`);

    await screen.findByTestId("topology-object-layout");
    topologyCanvasMock.fitView.mockClear();

    await user.click(screen.getByRole("button", { name: "适配" }));
    expect(topologyCanvasMock.fitView).toHaveBeenCalledWith();

    await user.click(screen.getByRole("button", { name: "重置" }));
    expect(topologyCanvasMock.fitView).toHaveBeenCalledWith("balanced");
  });

  it("shows isolate mode chrome and can return to the global topology page", async () => {
    const user = userEvent.setup();
    renderObjectRoute(`/topology/object/${encodedFocalServiceId}?mode=isolate`);

    await waitFor(() => {
      expect(screen.getByText("Isolate")).toBeInTheDocument();
    });

    const [backButton] = screen.getAllByRole("button");
    await user.click(backButton!);

    await waitFor(() => {
      expect(screen.getByTestId("topology-stage-workplane")).toBeInTheDocument();
    });
  });

  it("lets relation links switch to another object detail page", async () => {
    const user = userEvent.setup();
    renderObjectRoute(`/topology/object/${encodedFocalServiceId}`);

    const inspector = await screen.findByTestId("topology-object-inspector");
    const relationTab = inspector.querySelector('[id$="tab-relations"]');
    expect(relationTab).toBeInstanceOf(HTMLElement);
    await user.click(relationTab as HTMLElement);

    const relationLinks = await within(inspector).findAllByRole("button", {
      name: /wj-lab-ctl-02/i,
    });
    await user.click(relationLinks[0]!);

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 2, name: "wj-lab-ctl-02" })).toBeInTheDocument();
    });
  });

  it("supports old unencoded object detail links with slash in node id", async () => {
    renderObjectRoute("/topology/object/sw-200g:200GE1/0/1");

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 2, name: "200GE1/0/1" })).toBeInTheDocument();
    });

    await screen.findByTestId("topology-object-layout");
  });

  it("supports encoded object detail links with slash in node id", async () => {
    renderObjectRoute("/topology/object/sw-200g%3A200GE1%2F0%2F1");

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 2, name: "200GE1/0/1" })).toBeInTheDocument();
    });

    await screen.findByTestId("topology-object-layout");
  });
});
