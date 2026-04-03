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
  alert: alerts[0],
  status: "re_diagnosed",
  duration_seconds: 142,
};

function renderAlertsModifiedPage() {
  return render(
    <MemoryRouter initialEntries={["/alerts"]}>
      <Routes>
        <Route path="/alerts" element={<AlertsModifiedPage />} />
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
      isLoading: false,
    });

    vi.spyOn(apiClient, "getAlerts").mockResolvedValue({ alerts, clusters });
    vi.spyOn(apiClient, "getDiagnosisSession").mockResolvedValue(session);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the dense convergence workspace and supports grouped expansion", async () => {
    const user = userEvent.setup();
    renderAlertsModifiedPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "收敛结果看板" })).toBeInTheDocument();
    });

    expect(screen.queryByText("让值班同学只看最终处理结果，不再理解中间归并对象。")).not.toBeInTheDocument();
    expect(screen.queryByText("系统内部逻辑已收敛到结果视图")).not.toBeInTheDocument();
    expect(screen.getAllByText("2 个 fingerprint").length).toBeGreaterThan(0);
    expect(screen.getByText("压缩 1 条重复事件")).toBeInTheDocument();
    expect(screen.getByText("2 次事件")).toBeInTheDocument();
    expect(screen.getByText("重复折叠")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看 Session" })).toBeInTheDocument();
    expect(screen.getByText("第 2 次重试事件")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换 fp-001 事件流" }));
    expect(screen.queryByText("第 2 次重试事件")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换 fp-002 事件流" }));
    expect(screen.getByText("GPU 温度持续高于目标阈值")).toBeInTheDocument();
  });
});
