import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import App from "./App";
import { diagnosisHistorySessions } from "./mocks/data";
import { appRoutes } from "./routes";

describe("App shell", () => {
  it("renders topology together with main navigation and history channel sections", async () => {
    const topologyLabel = appRoutes.find((route) => route.key === "topology")?.label;
    const firstHistoryTitle = diagnosisHistorySessions[0]?.title;

    expect(topologyLabel).toBeTruthy();
    expect(firstHistoryTitle).toBeTruthy();

    const { container } = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("QinClaw")).toBeInTheDocument();
    });

    await screen.findByRole("button", { name: firstHistoryTitle! });

    expect(screen.getByText(/历史频道/)).toBeInTheDocument();
    expect(container.querySelector(".nav-section__label")?.textContent).toBe("主导航");
    expect(container.querySelectorAll(".nav-section")).toHaveLength(2);
    expect(screen.getByRole("button", { name: topologyLabel! })).toBeInTheDocument();
  });

  it("renders diagnosis inside the global shell while keeping the page as a chat workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/diagnosis/sess-latency-001"]}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "诊断对话" });

    expect(screen.getByText("QinClaw")).toBeInTheDocument();
    expect(screen.getByText("主导航")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "诊断" }).className).toContain("nav-item--active");
    expect(screen.getByText(/Session sess-latency-001/)).toBeInTheDocument();
  });

  it("collapses the sidebar into icon-only main navigation and expands again after selecting a feature", async () => {
    const topologyLabel = appRoutes.find((route) => route.key === "topology")?.label;
    const firstHistoryTitle = diagnosisHistorySessions[0]?.title;

    expect(topologyLabel).toBeTruthy();
    expect(firstHistoryTitle).toBeTruthy();

    const user = userEvent.setup();
    const { container } = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole("button", { name: firstHistoryTitle! });

    await user.click(screen.getByRole("button", { name: "收起侧边栏" }));

    expect(screen.getByRole("button", { name: "展开侧边栏" })).toBeInTheDocument();
    expect(screen.queryByText("历史频道")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".nav-section")).toHaveLength(1);
    expect(container.querySelectorAll(".nav-item__label")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: topologyLabel! }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "收起侧边栏" })).toBeInTheDocument();
    });

    expect(screen.getByText("历史频道")).toBeInTheDocument();
    expect(container.querySelectorAll(".nav-section")).toHaveLength(2);
  });

  it("keeps topology active when the legacy modified route is opened", async () => {
    const topologyLabel = appRoutes.find((route) => route.key === "topology")?.label;

    expect(topologyLabel).toBeTruthy();
    expect(appRoutes.some((route) => route.key === "topologyModified")).toBe(false);

    render(
      <MemoryRouter initialEntries={["/topology-modified"]}>
        <App />
      </MemoryRouter>,
    );

    const topologyButton = await screen.findByRole("button", { name: topologyLabel! });
    expect(topologyButton.className).toContain("nav-item--active");
  });

  it("does not expose a standalone chat route anymore", () => {
    expect(appRoutes.some((route) => route.key === "chat")).toBe(false);
  });

  it("marks history as the active route when a historical session is opened", async () => {
    const firstHistoryTitle = diagnosisHistorySessions[0]?.title;

    expect(firstHistoryTitle).toBeTruthy();

    render(
      <MemoryRouter initialEntries={["/history/sess-latency-001"]}>
        <App />
      </MemoryRouter>,
    );

    const historyButton = await screen.findByRole("button", { name: firstHistoryTitle! });
    expect(historyButton.className).toContain("nav-item--active");
  });

  it("renders subtle history status icons", async () => {
    const firstHistoryTitle = diagnosisHistorySessions[0]?.title;
    const diagnosingCount = diagnosisHistorySessions.filter((session) => !["resolved", "closed"].includes(session.status)).length;
    const completedCount = diagnosisHistorySessions.filter((session) => ["resolved", "closed"].includes(session.status)).length;

    expect(firstHistoryTitle).toBeTruthy();

    const { container } = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole("button", { name: firstHistoryTitle! });

    expect(container.querySelectorAll(".nav-item__status--diagnosing")).toHaveLength(diagnosingCount);
    expect(container.querySelectorAll(".nav-item__status--completed")).toHaveLength(completedCount);
  });

  it("exposes the modified alerts route as its own navigation entry", async () => {
    const modifiedAlertsLabel = appRoutes.find((route) => route.key === "alertsModified")?.label;

    expect(modifiedAlertsLabel).toBeTruthy();

    render(
      <MemoryRouter initialEntries={["/alerts-modified"]}>
        <App />
      </MemoryRouter>,
    );

    const modifiedAlertsButton = await screen.findByRole("button", { name: modifiedAlertsLabel! });
    expect(modifiedAlertsButton.className).toContain("nav-item--active");
  });
});

