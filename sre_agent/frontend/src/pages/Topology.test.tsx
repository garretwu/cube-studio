import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { createTopologyExplorerState, useTopologyExplorerStore } from "../features/topologyExplorer/store";
import TopologyObjectPage from "./TopologyObject";
import TopologyPage from "./Topology";

function renderTopologyRoutes(initialEntry = "/topology") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/topology/object/:nodeId/*" element={<TopologyObjectPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopologyPage", () => {
  beforeEach(() => {
    useTopologyExplorerStore.setState(createTopologyExplorerState());
  });

  it("renders the topology stage with room scope selected by default", async () => {
    renderTopologyRoutes();

    await waitFor(() => {
      expect(screen.getByTestId("topology-action-toolbar")).toBeInTheDocument();
    });

    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-view-mode", "graph");
    expect(screen.getByTestId("topology-room-selector")).toBeInTheDocument();
    expect(screen.queryByTestId("topology-side-inspector")).not.toBeInTheDocument();
  });

  it("switches between layered and tree views", async () => {
    const user = userEvent.setup();
    renderTopologyRoutes();

    const panel = await screen.findByTestId("topology-secondary-panel");
    const tabs = within(panel).getAllByRole("tab");

    await user.click(tabs[1]!);
    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-view-mode", "tree");

    await user.click(tabs[0]!);
    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-view-mode", "graph");
    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-layout-preset", "layered");
  });

  it("uses one shared filter panel for search and filter", async () => {
    const user = userEvent.setup();
    renderTopologyRoutes();

    const toolbar = await screen.findByTestId("topology-action-toolbar");
    const toolbarButtons = within(toolbar).getAllByRole("button");
    const searchButton = toolbarButtons[0]!;
    const filterButton = toolbarButtons[1]!;

    await user.click(searchButton);

    const panel = await screen.findByTestId("topology-filter-panel");
    const input = within(panel).getByRole("textbox");
    await user.type(input, "worker-01");

    await waitFor(() => {
      expect(within(panel).getByRole("button", { name: /BMC worker-01/i })).toBeInTheDocument();
    });

    await user.click(filterButton);
    expect(screen.getByTestId("topology-filter-panel")).toBe(panel);
  });

  it("shows a node action popover with Isolate and View topology actions", async () => {
    renderTopologyRoutes();

    const nodeButton = await screen.findByRole("button", { name: /^BMC worker-01 \|/i });
    fireEvent.click(nodeButton);

    const popover = await screen.findByTestId("topology-node-popover");
    expect(within(popover).getByRole("button", { name: "Isolate" })).toBeInTheDocument();
    expect(within(popover).getByRole("button", { name: "View topology" })).toBeInTheDocument();
  });

  it("navigates to the object topology page from the node action popover", async () => {
    const user = userEvent.setup();
    renderTopologyRoutes();

    const nodeButton = await screen.findByRole("button", { name: /^BMC worker-01 \|/i });
    fireEvent.click(nodeButton);
    await user.click(await screen.findByRole("button", { name: "View topology" }));

    await screen.findByTestId("topology-object-layout");
    expect(screen.getByTestId("topology-object-inspector")).toBeInTheDocument();
  });

  it("opens object detail for a port node whose id contains slash", async () => {
    const user = userEvent.setup();
    renderTopologyRoutes();

    const portButton = await screen.findByRole("button", { name: /^200GE1\/0\/1 \|/i });
    fireEvent.click(portButton);
    await user.click(await screen.findByRole("button", { name: "View topology" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 2, name: "200GE1/0/1" })).toBeInTheDocument();
    });

    await screen.findByTestId("topology-object-layout");
  });
});
