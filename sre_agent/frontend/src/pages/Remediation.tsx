import { useEffect, useMemo } from "react";

import type { SessionEvent } from "../api/types";
import ApprovalDialog from "../components/ApprovalDialog";
import CanaryProgress from "../components/CanaryProgress";
import { AppButton, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useRemediationStore } from "../store/remediationStore";
import { formatVerificationMethod, formatWorkflowStatus } from "../utils/display";
import { formatPercent, formatTimestamp } from "../utils/format";

type StepResult = {
  step_id?: number;
  tool?: string;
  command?: string;
  result?: unknown;
  success?: boolean;
  mocked?: boolean;
  message?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getEventStage(event: SessionEvent): string {
  if (event.type !== "remediation_progress") {
    return event.type;
  }
  const stage = String(event.data?.["stage"] ?? "").trim();
  return stage || "remediation_progress";
}

function getEventMessage(event: SessionEvent): string {
  const data = isRecord(event.data) ? event.data : {};
  if (typeof data.message === "string" && data.message.trim()) {
    return data.message;
  }
  const stage = getEventStage(event);
  if (stage === "execution_timeout") {
    return "修复执行超时退出";
  }
  if (stage === "execution_started") {
    return "执行修复中";
  }
  if (stage === "execution_succeeded") {
    return "修复执行成功";
  }
  if (stage === "execution_failed") {
    return "修复执行失败";
  }
  if (stage === "escalation_required") {
    return "需要工程师介入";
  }
  return stage;
}

function getStepResults(event: SessionEvent): StepResult[] {
  const data = isRecord(event.data) ? event.data : {};
  const raw = data.step_results;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter((item): item is StepResult => isRecord(item));
}

function formatResult(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

const TERMINAL_STATUSES = new Set(["resolved", "failed", "escalated", "timeout", "rejected"]);

function RemediationPage() {
  const { overview, events, approvalDialogOpen, fetchOverview, setApprovalDialogOpen, submitApproval } = useRemediationStore();

  useEffect(() => {
    void fetchOverview();
  }, [fetchOverview]);

  const progressStatus = overview?.progress.status ?? "pending";

  useEffect(() => {
    if (!overview?.session_id) {
      return;
    }
    if (progressStatus !== "remediating") {
      return;
    }
    const timer = window.setInterval(() => {
      void fetchOverview(overview.session_id);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [fetchOverview, overview?.session_id, progressStatus]);

  const orderedEvents = useMemo(() => {
    return [...events].sort((left, right) => left.timestamp.localeCompare(right.timestamp));
  }, [events]);

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          description="展示修复计划、审批状态、执行进度与观察结果。"
          eyebrow="执行控制"
          title="修复执行关口"
        />
        <SurfaceCard bodyClassName="page-stack" variant="hero">
          <div className="status-row">
            <StatusChip tone={TERMINAL_STATUSES.has(progressStatus) ? "success" : "warning"}>
              {formatWorkflowStatus(progressStatus, "加载中")}
            </StatusChip>
            <StatusChip tone="accent">{overview?.plan.priority ?? "P?"}</StatusChip>
            {overview?.plan.confidence ? <StatusChip tone="info">{formatPercent(overview.plan.confidence)}</StatusChip> : null}
            {overview?.plan_version ? <StatusChip tone="neutral">v{overview.plan_version}</StatusChip> : null}
          </div>
        </SurfaceCard>
      </div>

      <SurfaceCard
        actions={
          <AppButton disabled={!overview?.approval_required} onClick={() => setApprovalDialogOpen(true)} variant="primary">
            打开审批窗口
          </AppButton>
        }
        description="当前修复计划步骤与验证方式。"
        title="执行步骤"
      >
        <div className="mini-card-list">
          {(overview?.plan.steps ?? []).map((step) => (
            <div key={step.step_id} className="mini-card">
              <div className="status-row">
                <StatusChip tone="accent">步骤 {step.step_id}</StatusChip>
                <StatusChip tone="neutral">{step.tool}</StatusChip>
                <StatusChip tone="info">{formatVerificationMethod(step.verification.method)}</StatusChip>
              </div>
              <p className="mini-card__title">{step.description}</p>
              <p className="mini-card__copy">验证方式：{formatVerificationMethod(step.verification.method)}</p>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <SurfaceCard description="修复全过程事件流（审批、mock执行、观察、结论）。" title="修复时间线">
        <div className="mini-card-list">
          {orderedEvents.length === 0 ? (
            <div className="mini-card">
              <p className="mini-card__title">暂无事件</p>
              <p className="mini-card__copy">等待审批或执行触发。</p>
            </div>
          ) : (
            orderedEvents.map((event, index) => {
              const stage = getEventStage(event);
              const message = getEventMessage(event);
              const data = isRecord(event.data) ? event.data : {};
              const stepResults = getStepResults(event);
              const timeoutSeconds = data.timeout_seconds;
              return (
                <div key={`${event.type}-${event.timestamp}-${index}`} className="mini-card">
                  <div className="status-row">
                    <StatusChip tone="neutral">{stage}</StatusChip>
                    <StatusChip tone="info">{formatTimestamp(event.timestamp)}</StatusChip>
                  </div>
                  <p className="mini-card__title">{message}</p>
                  {typeof timeoutSeconds === "number" ? (
                    <p className="mini-card__copy">超时阈值：{timeoutSeconds} 秒</p>
                  ) : null}
                  {stepResults.length ? (
                    <div className="diagnosis-plan-steps">
                      {stepResults.map((item, itemIndex) => (
                        <div className="diagnosis-plan-step" key={`${stage}-${item.step_id ?? itemIndex}`}>
                          <p className="mini-card__copy">
                            步骤 {item.step_id ?? itemIndex + 1} | 工具：{item.tool ?? "-"} | 状态：
                            {item.success === false ? "失败" : "成功"}
                            {item.mocked ? "（mock）" : ""}
                          </p>
                          <p className="mini-card__copy">命令：{item.command ?? "-"}</p>
                          <p className="mini-card__copy">结果：{formatResult(item.result ?? item.message ?? "-")}</p>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </SurfaceCard>

      <SurfaceCard description="当前方案分批进度（若未定义批次则为空）。" title="金丝雀进度">
        <CanaryProgress batches={overview?.progress.batch_status ?? []} />
      </SurfaceCard>

      <ApprovalDialog
        onApprove={() => void submitApproval(true)}
        onCancel={() => setApprovalDialogOpen(false)}
        onReject={() => void submitApproval(false)}
        open={approvalDialogOpen}
        plan={overview?.plan}
      />
    </div>
  );
}

export default RemediationPage;
