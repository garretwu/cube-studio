import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { createTopologyExplorerState, useTopologyExplorerStore } from "../features/topologyExplorer/store";
import TopologyObjectPage from "./TopologyObject";
import TopologyPage from "./Topology";

function renderObjectRoute(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/topology/object/:nodeId" element={<TopologyObjectPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopologyObjectPage", () => {
  beforeEach(() => {
    useTopologyExplorerStore.setState(createTopologyExplorerState());
  });

  it("renders the two-column object topology layout for a focal object", async () => {
    renderObjectRoute("/topology/object/svc:monitoring:grafana");

    await waitFor(() => {
      expect(screen.getByTestId("topology-object-layout")).toBeInTheDocument();
    });

    expect(screen.getByTestId("topology-object-inspector")).toBeInTheDocument();
    expect(screen.getByText("直连对象关系图")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回全局拓扑" })).toBeInTheDocument();
  });

  it("shows isolate mode chrome and can return to the global topology page", async () => {
    const user = userEvent.setup();
    renderObjectRoute("/topology/object/svc:monitoring:grafana?mode=isolate");

    await waitFor(() => {
      expect(screen.getByText("Isolate")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "返回全局拓扑" }));

    await waitFor(() => {
      expect(screen.getByTestId("topology-stage-workplane")).toBeInTheDocument();
    });
  });

  it("lets relation links switch to another object detail page", async () => {
    const user = userEvent.setup();
    renderObjectRoute("/topology/object/svc:monitoring:grafana");

    await waitFor(() => {
      expect(screen.getByTestId("topology-object-inspector")).toBeInTheDocument();
    });

    await user.click(within(screen.getByTestId("topology-object-inspector")).getByRole("tab", { name: "关系" }));
    const relationLinks = await within(screen.getByTestId("topology-object-inspector")).findAllByRole("button", { name: /wj-lab-ctl-02/i });
    await user.click(relationLinks[0]!);

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 2, name: "wj-lab-ctl-02" })).toBeInTheDocument();
    });
  });
});

