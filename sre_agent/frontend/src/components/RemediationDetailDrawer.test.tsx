import { render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisSessionSummary, RemediationOverview, SessionEvent } from "../api/types";
import RemediationDetailDrawer from "./RemediationDetailDrawer";

function buildSummary(): DiagnosisSessionSummary {
  return {
    session_id: "sess-ttft-001",
    title: "AIServiceTTFTP99High",
    summary: "修复执行中",
    started_at: "2026-04-21T07:27:34Z",
    updated_at: "2026-04-21T07:27:34Z",
    status: "remediating",
    severity: "warning",
    alert_name: "AIServiceTTFTP99High",
    fingerprint: "fp-ttft-001",
    incident_key: "fpst:fp-ttft-001|2026-04-21T07:27:34Z",
    duration_seconds: 0,
    outcome: "proposed_fix_ready",
    triage_priority: "P1",
    root_cause: "异常进程抢占 GPU",
    affected_services: [],
  };
}

function buildOverview(): RemediationOverview {
  return {
    session_id: "sess-ttft-001",
    affected_services: ["vllm-serving", "chat-serving"],
    approval_required: true,
    plan: {
      plan_id: "plan-ttft-1",
      root_cause: "异常进程抢占 GPU",
      description: "结束异常进程并观察恢复",
      steps: [
        {
          step_id: 1,
          description: "结束异常进程",
          tool: "shell_exec",
          params: { command: "pkill -f fi_gpu_burn_gpu_cont" },
          verification: { method: "wait", wait_seconds: 30 },
          timeout: 60,
        },
      ],
      canary: null,
      estimated_impact: "低风险",
      confidence: 0.9,
      priority: "P1",
    },
    progress: {
      status: "remediating",
      completed_steps: 1,
      total_steps: 1,
      batch_status: [],
    },
    timeline: [],
  };
}

function buildEvents(): SessionEvent[] {
  return [
    {
      schema_version: "1.0",
      type: "token_delta",
      session_id: "sess-ttft-001",
      timestamp: "2026-04-21T07:26:00Z",
      data: { message: "should-not-show-token-delta" },
    } as SessionEvent,
    {
      schema_version: "1.0",
      type: "approval_required",
      session_id: "sess-ttft-001",
      timestamp: "2026-04-21T07:27:00Z",
      data: { message: "等待审批" },
    } as SessionEvent,
    {
      schema_version: "1.0",
      type: "remediation_progress",
      session_id: "sess-ttft-001",
      timestamp: "2026-04-21T07:28:00Z",
      data: {
        stage: "observation_result",
        message: "观察完成",
        policy_applied: "default_alert_and_metrics",
        alert_cleared: true,
        metrics_improved: true,
      },
    } as SessionEvent,
  ];
}

describe("RemediationDetailDrawer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-21T07:28:10Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fills affected services, shows observation strategy without canary, and filters non-remediation events", () => {
    const summary = buildSummary();
    const overview = buildOverview();
    const events = buildEvents();

    const { container } = render(
      <RemediationDetailDrawer
        open
        actionLoading={false}
        isLoading={false}
        onClose={() => {}}
        onOpenApproval={() => {}}
        record={{ summary, overview }}
        overview={overview}
        events={events}
      />,
    );

    const drawer = container.querySelector(".remediation-sidepanel");
    expect(drawer).toBeTruthy();
    const scoped = within(drawer as HTMLElement);

    expect(scoped.getByText("vllm-serving、chat-serving")).toBeInTheDocument();
    expect(scoped.getByText("策略口径：告警恢复 + 指标改善")).toBeInTheDocument();
    expect(scoped.getByText("alert_cleared：是")).toBeInTheDocument();
    expect(scoped.getByText("metrics_improved：是")).toBeInTheDocument();
    expect(scoped.getByText("观察结论：通过")).toBeInTheDocument();
    expect(scoped.queryByText("should-not-show-token-delta")).toBeNull();
  });

  it("renders live-derived duration and canary progress from remediation events", () => {
    const summary = buildSummary();
    const overview = buildOverview();
    overview.plan.canary = {
      enabled: true,
      target_percentage: 0.5,
      monitor_duration: 120,
      max_batches: 2,
      success_criteria: [],
    };
    overview.progress.status = "validating";
    overview.progress.completed_steps = 0;
    overview.progress.total_steps = 4;
    overview.timeline = [
      {
        schema_version: "1.0",
        type: "remediation_progress",
        session_id: "sess-ttft-001",
        timestamp: "2026-04-21T07:27:00Z",
        data: { stage: "execution_started" },
      },
      {
        schema_version: "1.0",
        type: "remediation_progress",
        session_id: "sess-ttft-001",
        timestamp: "2026-04-21T07:28:00Z",
        data: { stage: "canary_batch_completed", batch: "canary-1", batch_index: 1, batch_total: 2 },
      },
      {
        schema_version: "1.0",
        type: "remediation_progress",
        session_id: "sess-ttft-001",
        timestamp: "2026-04-21T07:28:05Z",
        data: { stage: "canary_batch_started", batch: "canary-2", batch_index: 2, batch_total: 2 },
      },
    ];

    const { container } = render(
      <RemediationDetailDrawer
        open
        actionLoading={false}
        isLoading={false}
        onClose={() => {}}
        onOpenApproval={() => {}}
        record={{ summary, overview }}
        overview={overview}
        events={overview.timeline}
      />,
    );

    const drawer = container.querySelector(".remediation-sidepanel");
    expect(drawer).toBeTruthy();
    const scoped = within(drawer as HTMLElement);

    expect(scoped.getByText("1分 10秒")).toBeInTheDocument();
    expect(scoped.getByText("总体 55%")).toBeInTheDocument();
    expect(scoped.getByText("灰度 75%")).toBeInTheDocument();

  });
});
