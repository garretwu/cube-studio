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

  it("renders the topology stage with room scope selected by default", async () => {
    renderTopologyRoutes();

    await waitFor(() => {
      expect(screen.getByTestId("topology-action-toolbar")).toBeInTheDocument();
    });

    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-view-mode", "graph");
    expect(screen.queryByRole("tab", { name: "机房视图" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "总览视图" })).not.toBeInTheDocument();
    expect(screen.getByTestId("topology-room-selector")).toBeInTheDocument();
    expect(screen.getByText("望京测试机房")).toBeInTheDocument();
    expect(screen.queryByTestId("topology-side-inspector")).not.toBeInTheDocument();
  });


  it("shows an expanded legend panel with counts and switches between layered, domain, and tree views", async () => {
    const user = userEvent.setup();
    renderTopologyRoutes();

    const panel = await screen.findByTestId("topology-secondary-panel");
    expect(within(panel).getByText("图例")).toBeInTheDocument();
    expect(within(panel).getByText("集群")).toBeInTheDocument();
    expect(within(panel).getByText("节点")).toBeInTheDocument();

    await user.click(within(panel).getByRole("tab", { name: "关系图（域布局）" }));
    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-layout-preset", "domain");
    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-view-mode", "graph");

    await user.click(within(panel).getByRole("tab", { name: "树视图" }));
    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-view-mode", "tree");
    expect(screen.getByRole("button", { name: "aidc-lab" })).toBeInTheDocument();

    await user.click(within(panel).getByRole("tab", { name: "关系图（层布局）" }));
    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-view-mode", "graph");
    expect(screen.getByTestId("topology-stage-workplane")).toHaveAttribute("data-layout-preset", "layered");
  });

  it("uses a compact icon action toolbar and reuses one shared filter panel for search and filter", async () => {
    const user = userEvent.setup();
    renderTopologyRoutes();

    const toolbar = await screen.findByTestId("topology-action-toolbar");
    const searchButton = within(toolbar).getByRole("button", { name: "搜索" });
    const filterButton = within(toolbar).getByRole("button", { name: "筛选" });

    expect(searchButton).toHaveAttribute("title", "搜索");
    expect(filterButton).toHaveAttribute("title", "筛选");
    expect(screen.queryByRole("button", { name: /切换域布局|切换层级布局/ })).not.toBeInTheDocument();

    await user.click(searchButton);

    const panel = await screen.findByTestId("topology-filter-panel");
    const input = within(panel).getByPlaceholderText("搜索机柜 / 节点 / GPU / 服务 / 交换机");
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

    await waitFor(() => {
      expect(screen.getByTestId("topology-object-layout")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "返回全局拓扑" })).toBeInTheDocument();
    expect(screen.getByTestId("topology-object-inspector")).toBeInTheDocument();
  });
});
