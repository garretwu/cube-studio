import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";

import type { DiagnosisSessionSummary } from "./api/types";
import App from "./App";
import { appRoutes } from "./routes";
import { server } from "./test/server";

const testHistorySessions: DiagnosisSessionSummary[] = [
  {
    session_id: "sess-latency-001",
    title: "3月18日推理变慢",
    summary: "vLLM 推理链路仍在修复中，金丝雀验证已通过，等待全量恢复观察。",
    started_at: "2026-03-18T12:00:00Z",
    updated_at: "2026-03-18T12:08:40Z",
    status: "remediating",
    severity: "critical",
    alert_name: "VLLM 延迟过高",
    duration_seconds: 182,
    outcome: "proposed_fix_ready",
    triage_priority: "P1",
    root_cause: "异常基准测试进程导致 GPU 资源争用",
    affected_services: ["vllm-latency", "chat-serving"],
    re_diagnosis_round: 1,
  },
  {
    session_id: "sess-gpu-temp-002",
    title: "GPU温度过高风险",
    summary: "单卡温度连续逼近安全阈值，已完成快速根因定位并建议限流观察。",
    started_at: "2026-03-17T08:24:00Z",
    updated_at: "2026-03-17T08:46:00Z",
    status: "resolved",
    severity: "warning",
    alert_name: "GPU 温度偏高",
    duration_seconds: 96,
    outcome: "resolved",
    triage_priority: "P2",
    root_cause: "机柜局部散热效率下降导致单卡温升异常。",
    affected_services: ["embedding-serving"],
    re_diagnosis_round: 0,
  },
  {
    session_id: "sess-rdma-loss-003",
    title: "RoCE 丢包突增",
    summary: "RDMA 网络丢包在训练高峰窗口骤升，完成一次回溯后转人工网络班继续收敛。",
    started_at: "2026-03-16T21:08:00Z",
    updated_at: "2026-03-16T21:33:00Z",
    status: "closed",
    severity: "critical",
    alert_name: "RoCE packet loss high",
    duration_seconds: 155,
    outcome: "escalated",
    triage_priority: "P1",
    root_cause: "核心交换链路突发拥塞，需网络侧联合排查 ECN 与队列水位。",
    affected_services: ["trainer-gateway", "rdma-sidecar"],
    re_diagnosis_round: 1,
  },
  {
    session_id: "sess-model-restart-004",
    title: "模型副本重启波动",
    summary: "推理服务副本在版本切换后出现短时重启抖动，已确认回滚后恢复。",
    started_at: "2026-03-15T14:12:00Z",
    updated_at: "2026-03-15T14:29:00Z",
    status: "resolved",
    severity: "info",
    alert_name: "Pod restart burst",
    duration_seconds: 74,
    outcome: "resolved",
    triage_priority: "P3",
    root_cause: "配置热加载与存量连接回收节奏不匹配。",
    affected_services: ["chat-serving"],
    re_diagnosis_round: 0,
  },
  {
    session_id: "sess-night-peak-005",
    title: "夜间推理波峰异常",
    summary: "夜间流量波峰期间单分片负载倾斜明显，当前结论已归档用于下次快速匹配。",
    started_at: "2026-03-14T23:41:00Z",
    updated_at: "2026-03-15T00:06:00Z",
    status: "diagnosed",
    severity: "warning",
    alert_name: "Inference shard skew",
    duration_seconds: 118,
    outcome: "proposed_fix_ready",
    triage_priority: "P2",
    root_cause: "流量分片权重未随夜间热点模型同步刷新。",
    affected_services: ["night-batch-router", "vllm-latency"],
    re_diagnosis_round: 0,
  },
];

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

  it("renders the new diagnosis page inside the global shell", async () => {
    const diagnosisLabel = appRoutes.find((route) => route.key === "diagnosis")?.label;
    expect(diagnosisLabel).toBeTruthy();

    const { container } = render(
      <MemoryRouter initialEntries={["/diagnosis/sess-latency-001"]}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "诊断（修改）" });

    expect(screen.getByText("QinClaw")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: diagnosisLabel! }).className).toContain("nav-item--active");
    expect(container.querySelector(".diagnosis-modified-shell")).toBeTruthy();
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

  it("does not expose the legacy alerts route in navigation", () => {
    expect(appRoutes.some((route) => route.key === "alerts")).toBe(false);
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

    expect(screen.getByRole("heading", { name: "诊断（修改）" })).toBeInTheDocument();
    expect(container.querySelector(".diagnosis-modified-shell")).toBeTruthy();
  });

  it("renders subtle history status icons", async () => {
    server.use(
      http.get("/api/sessions", async () =>
        HttpResponse.json(
          testHistorySessions.map((item) => ({
            session_id: item.session_id,
            status: item.status,
            alert_name: item.alert_name,
            severity: item.severity,
            fingerprint: item.session_id,
            outcome: item.outcome ?? null,
            duration_seconds: item.duration_seconds,
            updated_at: item.updated_at,
          })),
        ),
      ),
    );

    const diagnosingCount = testHistorySessions.filter((session) => !["resolved", "closed"].includes(session.status)).length;
    const completedCount = testHistorySessions.filter((session) => ["resolved", "closed"].includes(session.status)).length;

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
    server.use(
      http.get("/api/sessions", async () =>
        HttpResponse.json(
          testHistorySessions.map((item) => ({
            session_id: item.session_id,
            status: item.status,
            alert_name: item.alert_name,
            severity: item.severity,
            fingerprint: item.session_id,
            outcome: item.outcome ?? null,
            duration_seconds: item.duration_seconds,
            updated_at: item.updated_at,
          })),
        ),
      ),
    );

    const { container } = render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(container.querySelectorAll(".nav-item--history").length).toBeGreaterThan(0);
    });

    const firstHistoryItem = container.querySelector(".nav-item--history");
    expect(firstHistoryItem?.querySelector(".nav-item__label")?.textContent).toBe(testHistorySessions[0]?.alert_name);
    expect(firstHistoryItem?.querySelector(".nav-item__meta")?.textContent).toBe(
      testHistorySessions[0]?.severity.toUpperCase(),
    );
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

  it("redirects /alerts to /alerts-modified and keeps alerts navigation active", async () => {
    const modifiedAlertsLabel = appRoutes.find((route) => route.key === "alertsModified")?.label;
    expect(modifiedAlertsLabel).toBeTruthy();

    render(
      <MemoryRouter initialEntries={["/alerts"]}>
        <App />
      </MemoryRouter>,
    );

    const modifiedAlertsButton = await screen.findByRole("button", { name: modifiedAlertsLabel! });
    expect(modifiedAlertsButton.className).toContain("nav-item--active");
  });
});
