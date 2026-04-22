import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import App, { resolvePageChrome } from "./App";
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
    expect(container.querySelector("main.shell-content--workspace-page")).toBeTruthy();
    expect(container.querySelector(".topology-modified-stage--canvas-only")).toBeTruthy();
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

    await screen.findByTestId("diagnosis-modified-split-workspace");

    expect(screen.getByText("QinClaw")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: diagnosisLabel! }).className).toContain("nav-item--active");
    expect(document.querySelector(".diagnosis-modified-page")).toBeTruthy();
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

  it("redirects the legacy modified topology route to /topology", async () => {
    render(
      <MemoryRouter initialEntries={["/topology-modified"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "AIDC档案" }).className).toContain("nav-item--active");
    });
  });

  it("keeps topology active when an object topology detail route is opened", async () => {
    const topologyLabel = appRoutes.find((route) => route.key === "topology")?.label;

    expect(topologyLabel).toBeTruthy();

    render(
      <MemoryRouter initialEntries={["/topology/object/gpu-03"]}>
        <App />
      </MemoryRouter>,
    );

    const topologyButton = await screen.findByRole("button", { name: topologyLabel! });
    expect(topologyButton.className).toContain("nav-item--active");
  });
  it("shows subtitles on first-level module pages only", async () => {
    const topologyChrome = resolvePageChrome("/topology");
    const topologyDetailChrome = resolvePageChrome("/topology/object/gpu-03");
    const knowledgeChrome = resolvePageChrome("/knowledge");
    const knowledgeDetailChrome = resolvePageChrome("/knowledge/builtin-kb");

    const topologyRoot = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText(topologyChrome.subtitle!)).toBeInTheDocument();
    topologyRoot.unmount();

    const topologyDetail = render(
      <MemoryRouter initialEntries={["/topology/object/gpu-03"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { level: 2, name: topologyDetailChrome.title! })).toBeInTheDocument();
    expect(screen.queryByText(topologyChrome.subtitle!)).not.toBeInTheDocument();
    topologyDetail.unmount();

    const knowledgeRoot = render(
      <MemoryRouter initialEntries={["/knowledge"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText(knowledgeChrome.subtitle!)).toBeInTheDocument();
    knowledgeRoot.unmount();

    const knowledgeDetail = render(
      <MemoryRouter initialEntries={["/knowledge/builtin-kb"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { level: 2, name: knowledgeDetailChrome.title! })).toBeInTheDocument();
    expect(screen.queryByText(knowledgeChrome.subtitle!)).not.toBeInTheDocument();
    knowledgeDetail.unmount();
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

  it("renders history alert names and severities in separate slots", async () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(container.querySelectorAll(".nav-item--history").length).toBeGreaterThan(0);
    });

    const firstHistoryItem = container.querySelector(".nav-item--history");
    expect(firstHistoryItem?.querySelector(".nav-item__label")?.textContent).toBe(diagnosisHistorySessions[0]?.alert_name);
    expect(firstHistoryItem?.querySelector(".nav-item__meta")?.textContent).toBe(
      diagnosisHistorySessions[0]?.severity.toUpperCase(),
    );
  });


  it("exposes the alerts route as its own navigation entry", async () => {
    const alertsLabel = appRoutes.find((route) => route.key === "alertsModified")?.label;

    expect(alertsLabel).toBeTruthy();
    expect(appRoutes.some((route) => route.key === "alerts")).toBe(false);

    render(
      <MemoryRouter initialEntries={["/alerts"]}>
        <App />
      </MemoryRouter>,
    );

    const modifiedAlertsButton = await screen.findByRole("button", { name: alertsLabel! });
    expect(modifiedAlertsButton.className).toContain("nav-item--active");
  });
});



