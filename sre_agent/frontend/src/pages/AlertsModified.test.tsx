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
    alert_name: "VLLM 寤惰繜杩囬珮",
    severity: "critical",
    labels: { service: "vllm", instance: "vllm-0", aidc: "aidc-001" },
    annotations: { summary: "p95 寤惰繜瓒呰繃 600ms", entity: "svc-vllm" },
    starts_at: "2026-03-18T12:00:00Z",
    fingerprint: "fp-001",
    status: "firing",
    source: "alertmanager",
  },
  {
    alert_name: "VLLM 寤惰繜杩囬珮",
    severity: "critical",
    labels: { service: "vllm", instance: "vllm-0", aidc: "aidc-001" },
    annotations: { summary: "绗?2 娆￠噸璇曚簨浠?, entity: "svc-vllm" },
    starts_at: "2026-03-18T12:01:00Z",
    fingerprint: "fp-001",
    status: "firing",
    source: "alertmanager",
  },
  {
    alert_name: "GPU 娓╁害鍋忛珮",
    severity: "warning",
    labels: { node: "node-gpu-01", gpu: "gpu-01", aidc: "aidc-001" },
    annotations: { summary: "GPU 娓╁害鎸佺画楂樹簬鐩爣闃堝€?, entity: "gpu-01" },
    starts_at: "2026-03-18T11:57:00Z",
    fingerprint: "fp-002",
    status: "firing",
    source: "prometheus",
  },
];

const clusters: AlertCluster[] = [
  {
    cluster_id: "cluster-01",
    summary: "寤惰繜绐佸涓庡崟涓帹鐞嗚妭鐐圭殑鐑帇寮傚父楂樺害鐩稿叧",
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
    <MemoryRouter initialEntries={["/alerts-modified"]}>
      <Routes>
        <Route path="/alerts-modified" element={<AlertsModifiedPage />} />
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
      expect(screen.getByRole("heading", { name: "告警收敛" })).toBeInTheDocument();
    });

    expect(screen.queryByText("璁╁€肩彮鍚屽鍙湅鏈€缁堝鐞嗙粨鏋滐紝涓嶅啀鐞嗚В涓棿褰掑苟瀵硅薄銆?)).not.toBeInTheDocument();
    expect(screen.queryByText("绯荤粺鍐呴儴閫昏緫宸叉敹鏁涘埌缁撴灉瑙嗗浘")).not.toBeInTheDocument();
    expect(screen.getAllByText("2 涓?fingerprint").length).toBeGreaterThan(0);
    expect(screen.getByText("鍘嬬缉 1 鏉￠噸澶嶄簨浠?)).toBeInTheDocument();
    expect(screen.getByText("2 娆′簨浠?)).toBeInTheDocument();
    expect(screen.getByText("閲嶅鎶樺彔")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "鏌ョ湅 Session" })).toBeInTheDocument();
    expect(screen.getByText("绗?2 娆￠噸璇曚簨浠?)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "鍒囨崲 fp-001 浜嬩欢娴? }));
    expect(screen.queryByText("绗?2 娆￠噸璇曚簨浠?)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "鍒囨崲 fp-002 浜嬩欢娴? }));
    expect(screen.getByText("GPU 娓╁害鎸佺画楂樹簬鐩爣闃堝€?)).toBeInTheDocument();
  });
});

