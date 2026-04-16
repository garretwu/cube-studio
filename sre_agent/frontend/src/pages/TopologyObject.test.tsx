import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { createTopologyExplorerState, useTopologyExplorerStore } from "../features/topologyExplorer/store";
import TopologyObjectPage from "./TopologyObject";
import TopologyPage from "./Topology";

vi.mock("../features/topologyExplorer/components/TopologyCanvas", () => {
  const MockTopologyCanvas = React.forwardRef(function MockTopologyCanvas() {
    return <div data-testid="mock-topology-canvas" />;
  });

  return {
    __esModule: true,
    default: MockTopologyCanvas,
  };
});

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
  const focalServiceId = "svc-ns:monitoring";
  const encodedFocalServiceId = encodeURIComponent(focalServiceId);

  beforeEach(() => {
    useTopologyExplorerStore.setState(createTopologyExplorerState());
  });

  it("renders the two-column object topology layout for a focal object", async () => {
    renderObjectRoute(`/topology/object/${encodedFocalServiceId}`);

    await waitFor(() => {
      expect(screen.getByTestId("topology-object-layout")).toBeInTheDocument();
    });

    expect(screen.getByTestId("topology-object-inspector")).toBeInTheDocument();
    expect(screen.getByTestId("mock-topology-canvas")).toBeInTheDocument();
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
