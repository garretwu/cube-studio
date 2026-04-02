import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import App from "./App";
import { diagnosisHistorySessions } from "./mocks/data";
import { appRoutes } from "./routes";

describe("App shell", () => {
  it("renders topology together with main navigation and history channel sections", async () => {
    const topologyLabel = appRoutes.find((route) => route.key === "topology")?.label;

    expect(topologyLabel).toBeTruthy();

    const { container } = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("QinClaw")).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(container.querySelectorAll(".nav-item--history").length).toBeGreaterThan(0);
    });

    expect(container.querySelectorAll(".nav-section")).toHaveLength(2);
    expect(screen.getByRole("button", { name: topologyLabel! })).toBeInTheDocument();
  });

  it("renders diagnosis inside the global shell while keeping the page as a chat workspace", async () => {
    const diagnosisLabel = appRoutes.find((route) => route.key === "diagnosis")?.label;
    expect(diagnosisLabel).toBeTruthy();

    render(
      <MemoryRouter initialEntries={["/diagnosis/sess-latency-001"]}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "诊断对话" });

    expect(screen.getByText("QinClaw")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: diagnosisLabel! }).className).toContain("nav-item--active");
    expect(screen.getByText(/会话 sess-latency-001/)).toBeInTheDocument();
  });

  it("collapses the sidebar into icon-only main navigation and expands again after selecting a feature", async () => {
    const topologyLabel = appRoutes.find((route) => route.key === "topology")?.label;

    expect(topologyLabel).toBeTruthy();

    const user = userEvent.setup();
    const { container } = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(container.querySelectorAll(".nav-item--history").length).toBeGreaterThan(0);
    });

    const collapseButton = container.querySelector<HTMLButtonElement>(".shell-brand__collapse");
    expect(collapseButton).toBeTruthy();
    await user.click(collapseButton!);

    expect(container.querySelector(".shell-root--sidebar-collapsed")).toBeTruthy();
    expect(container.querySelectorAll(".nav-section")).toHaveLength(1);
    expect(container.querySelectorAll(".nav-item__label")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: topologyLabel! }));

    await waitFor(() => {
      expect(container.querySelector(".shell-root--sidebar-collapsed")).toBeFalsy();
    });

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
    const { container } = render(
      <MemoryRouter initialEntries={["/history/sess-latency-001"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(container.querySelector(".nav-item--history.nav-item--active")).toBeTruthy();
    });
  });

  it("renders subtle history status icons", async () => {
    const diagnosingCount = diagnosisHistorySessions.filter((session) => !["resolved", "closed"].includes(session.status)).length;
    const completedCount = diagnosisHistorySessions.filter((session) => ["resolved", "closed"].includes(session.status)).length;

    const { container } = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(container.querySelectorAll(".nav-item--history").length).toBeGreaterThan(0);
    });

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

