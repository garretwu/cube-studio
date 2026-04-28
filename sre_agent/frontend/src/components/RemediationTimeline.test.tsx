import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SessionEvent } from "../api/types";
import RemediationTimeline from "./RemediationTimeline";

describe("RemediationTimeline", () => {
  it("renders unique canary and observation stages once when the backend emits one event per stage", () => {
    const events: SessionEvent[] = [
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-canary-unique",
        timestamp: "2026-04-17T15:16:00.000Z",
        data: {
          event_id: "101",
          stage: "canary_batch_started",
          batch: "canary-1",
          batch_index: 1,
          batch_total: 2,
          message: "灰度批次 1/2 开始，覆盖 1 个目标: proc:3802685",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-canary-unique",
        timestamp: "2026-04-17T15:18:00.000Z",
        data: {
          event_id: "102",
          stage: "canary_check_passed",
          batch: "canary-1",
          batch_index: 1,
          message: "验证灰度批次 1 的成功条件 (观察窗口 120s)",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-canary-unique",
        timestamp: "2026-04-17T15:18:01.000Z",
        data: {
          event_id: "103",
          stage: "canary_batch_completed",
          batch: "canary-1",
          batch_index: 1,
          batch_total: 2,
          message: "灰度批次 1/2 完成，覆盖 1 个目标",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-canary-unique",
        timestamp: "2026-04-17T15:20:00.000Z",
        data: {
          event_id: "104",
          stage: "observation_started",
          seconds: 120,
          poll_interval_seconds: 10,
          message: "进入观察阶段，持续 120 秒",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-canary-unique",
        timestamp: "2026-04-17T15:22:00.000Z",
        data: {
          event_id: "105",
          stage: "observation_result",
          alert_cleared: true,
          metrics_improved: true,
          message: "观察结果：alert_cleared=true，metrics_improved=true，告警状态 firing -> resolved",
        },
      },
    ];

    render(<RemediationTimeline events={events} sessionId="sess-canary-unique" />);

    expect(screen.getAllByText("灰度批次 1/2 开始，覆盖 1 个目标: proc:3802685")).toHaveLength(1);
    expect(screen.getAllByText("验证灰度批次 1 的成功条件 (观察窗口 120s)")).toHaveLength(1);
    expect(screen.getAllByText("灰度批次 1/2 完成，覆盖 1 个目标")).toHaveLength(1);
    expect(screen.getAllByText("进入观察阶段，持续 120 秒")).toHaveLength(1);
    expect(screen.getAllByText("观察结果：alert_cleared=true，metrics_improved=true，告警状态 firing -> resolved")).toHaveLength(1);
  });

  it("renders known remediation stages and legacy English messages in Chinese", () => {
    const events: SessionEvent[] = [
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-localized",
        timestamp: "2026-04-28T10:42:00.000Z",
        data: {
          stage: "approval_accepted",
          message: "remediation status update",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-localized",
        timestamp: "2026-04-28T10:43:00.000Z",
        data: {
          stage: "remediating",
          message: "正在执行步骤 4/3: Terminate suspect process PID 1012427 on node worker-03",
        },
      },
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-localized",
        timestamp: "2026-04-28T10:44:00.000Z",
        data: {
          stage: "validating",
          message: "Target process 1012427 was not found; treating this kill step as successful.",
        },
      },
    ];

    render(<RemediationTimeline events={events} sessionId="sess-localized" />);

    expect(screen.getByText("审批已通过")).toBeInTheDocument();
    expect(screen.getByText("正在执行步骤 3/3：终止可疑进程 PID 1012427，位于节点 worker-03")).toBeInTheDocument();
    expect(screen.getByText("目标进程 1012427 未找到，视为该 kill_process 步骤成功")).toBeInTheDocument();
    expect(screen.queryByText("remediation status update")).not.toBeInTheDocument();
  });

  it("uses display_step_index before the original audit step_id", () => {
    const events: SessionEvent[] = [
      {
        schema_version: "1",
        type: "remediation_progress",
        session_id: "sess-step-index",
        timestamp: "2026-04-28T10:45:00.000Z",
        data: {
          stage: "execution_succeeded",
          message: "修复执行成功",
          step_results: [
            {
              step_id: 4,
              display_step_index: 3,
              tool: "kill_process",
              command: "kill -TERM -- 1012427",
              result: "ok",
              success: true,
            },
          ],
        },
      },
    ];

    render(<RemediationTimeline events={events} sessionId="sess-step-index" />);
    fireEvent.click(screen.getByRole("button", { name: /查看 修复执行成功 的详情/ }));

    expect(screen.getByText("步骤 3")).toBeInTheDocument();
    expect(screen.queryByText("步骤 4")).not.toBeInTheDocument();
  });
});
