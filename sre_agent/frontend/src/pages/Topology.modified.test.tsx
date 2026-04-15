import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { createTopologyExplorerState, useTopologyExplorerStore } from "../features/topologyExplorer/store";
import TopologyPage from "./Topology";

function renderModifiedTopologyPage() {
  return render(
    <MemoryRouter initialEntries={["/topology-modified"]}>
      <Routes>
        <Route path="/topology-modified" element={<TopologyPage variant="modified" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopologyPage modified variant", () => {
  beforeEach(() => {
    useTopologyExplorerStore.setState(createTopologyExplorerState());
  });

  it("renders the modified stage with aggregate groups and keeps aggregate clicks out of the object popover", async () => {
    renderModifiedTopologyPage();

    await waitFor(() => {
      expect(screen.getByTestId("topology-stage-workplane")).toBeInTheDocument();
    });

    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-topology-variant", "modified");

    const canvas = await screen.findByTestId("topology-canvas");
    await waitFor(() => {
      expect(
        canvas.querySelectorAll(".topology-flow-node--aggregate .topology-flow-node__meta").length,
      ).toBeGreaterThan(0);
    });

    const aggregateMeta = Array.from(
      canvas.querySelectorAll(".topology-flow-node--aggregate .topology-flow-node__meta"),
    );
    const aggregateButton = aggregateMeta[0]?.closest("button");
    expect(aggregateButton).not.toBeNull();

    fireEvent.click(aggregateButton!);

    await waitFor(() => {
      expect(screen.queryByTestId("topology-node-popover")).not.toBeInTheDocument();
    });
  });
});
