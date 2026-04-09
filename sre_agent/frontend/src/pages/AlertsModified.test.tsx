import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { apiClient } from "../api/client";
import type { Alert, AlertCluster, DiagnosisSession } from "../api/types";
import { useAlertStore } from "../store/alertStore";
import AlertsModifiedPage from "./AlertsModified";

const alerts: Alert[] = [
  {
    alert_name: "VLLM 延迟过高",
    severity: "critical",
    labels: { service: "vllm", instance: "vllm-0", aidc: "aidc-001" },
    annotations: { summary: "p95 延迟超过 600ms", entity: "svc-vllm" },
    starts_at: "2026-03-18T12:00:00Z",
    fingerprint: "fp-001",
    status: "firing",
    source: "alertmanager",
  },
  {
    alert_name: "VLLM 延迟过高",
    severity: "critical",
    labels: { service: "vllm", instance: "vllm-0", aidc: "aidc-001" },
    annotations: { summary: "第 2 次重试事件", entity: "svc-vllm" },
    starts_at: "2026-03-18T12:01:00Z",
    fingerprint: "fp-001",
    status: "firing",
    source: "alertmanager",
  },
  {
    alert_name: "GPU 温度偏高",
    severity: "warning",
    labels: { node: "node-gpu-01", gpu: "gpu-01", aidc: "aidc-001" },
    annotations: { summary: "GPU 温度持续高于目标阈值", entity: "gpu-01" },
    starts_at: "2026-03-18T11:57:00Z",
    fingerprint: "fp-002",
    status: "firing",
    source: "prometheus",
  },
];

const clusters: AlertCluster[] = [
  {
    cluster_id: "cluster-01",
    summary: "延迟突增与单个推理节点的热压异常高度相关",
    severity: "critical",
    alerts: ["fp-001", "fp-002"],
  },
];

const session: DiagnosisSession = {
  session_id: "sess-latency-001",
  alert: alerts[0]!,
  status: "re_diagnosed",
  duration_seconds: 142,
};

function renderAlertsModifiedPage(initialRoute = "/alerts-modified") {
  return render(
    <MemoryRouter initialEntries={[initialRoute]}>
      <Routes>
        <Route path="/alerts-modified" element={<AlertsModifiedPage />} />
        <Route path="/diagnosis/:sessionId" element={<div>diagnosis page reached</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AlertsModifiedPage", () => {
  beforeEach(() => {
    useAlertStore.setState({
      alerts: [],
      clusters: [],
      severityFilter: "all",
      wsState: "closed",
      lastAlertEventAt: undefined,
      lastSnapshotSyncAt: undefined,
      realtimeEnabled: false,
      isLoading: false,
      hasLoaded: false,
      error: undefined,
    });

    vi.spyOn(apiClient, "getAlerts").mockResolvedValue({ alerts, clusters });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders convergence workspace and supports grouped expansion", async () => {
    const user = userEvent.setup();
    vi.spyOn(apiClient, "getDiagnosisSession").mockResolvedValue(session);

    renderAlertsModifiedPage();

    expect(await screen.findByRole("heading", { name: "告警收敛" })).toBeInTheDocument();
    expect(screen.getAllByText("2 个 fingerprint").length).toBeGreaterThan(0);
    expect(screen.getByText("2 次事件")).toBeInTheDocument();
    expect(screen.getByText("收敛结果")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看诊断" })).toBeInTheDocument();
    expect(screen.getAllByText("并入已有诊断").length).toBeGreaterThan(0);
    expect(screen.getByText("第 2 次重试事件")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换 fp-001 事件流" }));
    expect(screen.queryByText("第 2 次重试事件")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换 fp-002 事件流" }));
    expect(screen.getByText("GPU 温度持续高于目标阈值")).toBeInTheDocument();
  });

  it("starts diagnosis via new async API and navigates immediately", async () => {
    const user = userEvent.setup();
    vi.spyOn(apiClient, "getDiagnosisSession").mockResolvedValue(null);
    vi.spyOn(apiClient, "startDiagnoseAlert").mockResolvedValue({
      session_id: "sess-created-001",
      alert: alerts[0],
      status: "diagnosing",
      duration_seconds: 0,
    });

    renderAlertsModifiedPage();

    await screen.findByRole("heading", { name: "告警收敛" });

    await user.click(screen.getByRole("button", { name: "进入诊断" }));

    await waitFor(() => {
      expect(apiClient.startDiagnoseAlert).toHaveBeenCalledTimes(1);
      expect(apiClient.startDiagnoseAlert).toHaveBeenCalledWith(
        expect.objectContaining({
          fingerprint: "fp-001",
        }),
        expect.arrayContaining(["fp-002"]),
      );
    });
    expect(await screen.findByText("diagnosis page reached")).toBeInTheDocument();
  });
});
