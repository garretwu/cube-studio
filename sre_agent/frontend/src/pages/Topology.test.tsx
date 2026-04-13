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
        <Route path="/topology/object/:nodeId" element={<TopologyObjectPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopologyPage", () => {
  beforeEach(() => {
    useTopologyExplorerStore.setState(createTopologyExplorerState());
  });

  it("renders a single canvas-first stage without summary pills or a persistent side inspector", async () => {
    renderTopologyRoutes();

    await waitFor(
      () => {
        expect(screen.getByTestId("topology-filter-dock")).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    expect(screen.getByTestId("topology-stage-workplane")).toBeInTheDocument();
    expect(screen.queryByTestId("topology-side-inspector")).not.toBeInTheDocument();
    expect(screen.queryByText("总实体")).not.toBeInTheDocument();
  });

  it("opens the in-canvas filter panel and shows live search-filter results", async () => {
    const user = userEvent.setup();
    renderTopologyRoutes();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "筛选" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "筛选" }));

    const panel = await screen.findByTestId("topology-filter-panel");
    const input = within(panel).getByPlaceholderText("搜索机柜 / 节点 / GPU / 服务 / 交换机");
    await user.type(input, "worker-01");

    await waitFor(() => {
      expect(within(panel).getByRole("button", { name: /BMC worker-01/i })).toBeInTheDocument();
    });

    expect(within(panel).queryByText("当前筛选条件下没有匹配对象。")).not.toBeInTheDocument();
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

    await waitFor(() => {
      expect(screen.getByTestId("topology-object-layout")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "返回全局拓扑" })).toBeInTheDocument();
    expect(screen.getByTestId("topology-object-inspector")).toBeInTheDocument();
  });
});