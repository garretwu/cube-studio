import { describe, expect, it } from "vitest";

import type { Alert, AlertCluster, DiagnosisSession } from "../api/types";
import { buildAlertConvergenceView } from "./alertsModifiedModel";

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

const activeSession: DiagnosisSession = {
  session_id: "sess-latency-001",
  alert: alerts[0]!,
  status: "re_diagnosed",
  duration_seconds: 142,
};

describe("buildAlertConvergenceView", () => {
  it("compresses events by fingerprint and preserves result metrics", () => {
    const view = buildAlertConvergenceView(alerts, clusters, activeSession);

    expect(view.flow).toHaveLength(2);
    expect(view.flow[0].fingerprint).toBe("fp-001");
    expect(view.flow[0].eventCount).toBe(2);
    expect(view.flow[0].events).toHaveLength(2);
    expect(view.flow[0].events[0].startsAt).toBe("2026-03-18T12:01:00Z");
    expect(view.flow[0].fingerprintRole).toBe("主指纹");

    expect(view.results).toHaveLength(1);
    expect(view.results[0].alertCount).toBe(3);
    expect(view.results[0].fingerprintCount).toBe(2);
    expect(view.results[0].duplicateFoldedCount).toBe(1);
    expect(view.results[0].route.label).toBe("并入已有诊断");
  });
});
