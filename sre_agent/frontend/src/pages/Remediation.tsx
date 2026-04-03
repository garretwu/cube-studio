import { useEffect, useMemo, useState } from "react";
import { Select } from "antd";
import { useNavigate, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { SessionEvent, SessionSummary } from "../api/types";
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

const TERMINAL_STATUSES = new Set(["resolved", "failed", "escalated", "timeout", "rejected"]);
const REMEDIATION_KEY_STAGES = new Set([
  "approval_accepted",
  "approval_rejected",
  "execution_started",
  "execution_mocked",
  "observation_started",
  "observation_result",
  "execution_succeeded",
  "execution_failed",
  "execution_timeout",
  "escalation_required",
  "rollback_started",
  "rollback_succeeded",
  "rollback_failed",
]);

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

function isRemediationTimelineEvent(event: SessionEvent): boolean {
  if (event.type === "approval_required" || event.type === "plan_revised") {
    return true;
  }
  if (event.type !== "remediation_progress") {
    return false;
  }
  const stage = String(event.data?.["stage"] ?? "").trim().toLowerCase();
  return REMEDIATION_KEY_STAGES.has(stage);
}

function getEventMessage(event: SessionEvent): string {
  const data = isRecord(event.data) ? event.data : {};
  if (typeof data.message === "string" && data.message.trim()) {
    return data.message;
  }
  if (event.type === "approval_required") {
    return "等待审批";
  }
  if (event.type === "plan_revised") {
    return "修复方案已更新";
  }
  const stage = getEventStage(event).toLowerCase();
  if (stage === "approval_accepted") {
    return "审批通过，准备执行修复";
  }
  if (stage === "approval_rejected") {
    return "审批拒绝";
  }
  if (stage === "execution_started") {
    return "执行修复中";
  }
  if (stage === "execution_mocked") {
    return "模拟执行完成";
  }
  if (stage === "observation_started") {
    return "开始观察修复效果";
  }
  if (stage === "observation_result") {
    return "观察结果已返回";
  }
  if (stage === "execution_succeeded") {
    return "修复执行成功";
  }
  if (stage === "execution_failed") {
    return "修复执行失败";
  }
  if (stage === "execution_timeout") {
    return "修复执行超时";
  }
  if (stage === "escalation_required") {
    return "需要工程师介入";
  }
  if (stage === "rollback_started") {
    return "开始回滚";
  }
  if (stage === "rollback_succeeded") {
    return "回滚成功";
  }
  if (stage === "rollback_failed") {
    return "回滚失败";
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

function sessionOptionLabel(session: SessionSummary): string {
  return `${session.session_id} | ${session.alert_name} | ${formatWorkflowStatus(session.status)}`;
}

function RemediationPage() {
  const navigate = useNavigate();
  const { sessionId: sessionIdFromRoute } = useParams<{ sessionId?: string }>();
  const { overview, events, approvalDialogOpen, fetchOverview, setApprovalDialogOpen, submitApproval, setSessionId } =
    useRemediationStore();

  const [sessionOptions, setSessionOptions] = useState<SessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);
  const [sessionLoadError, setSessionLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadSessions = async () => {
      setIsLoadingSessions(true);
      setSessionLoadError(null);
      try {
        const sessions = await apiClient.getSessions(50);
        if (cancelled) {
          return;
        }
        setSessionOptions(sessions);

        const routeSessionId = String(sessionIdFromRoute ?? "").trim();
        const targetSessionId = routeSessionId || sessions[0]?.session_id || "";
        if (!targetSessionId) {
          setSelectedSessionId("");
          return;
        }
        setSelectedSessionId(targetSessionId);
        setSessionId(targetSessionId);
        await fetchOverview(targetSessionId);
      } catch (error) {
        if (!cancelled) {
          setSessionLoadError(error instanceof Error ? error.message : "会话加载失败");
        }
      } finally {
        if (!cancelled) {
          setIsLoadingSessions(false);
        }
      }
    };

    void loadSessions();
    return () => {
      cancelled = true;
    };
  }, [fetchOverview, setSessionId, sessionIdFromRoute]);

  const progressStatus = overview?.progress.status ?? "pending";

  useEffect(() => {
    if (!selectedSessionId) {
      return;
    }
    if (progressStatus !== "remediating") {
      return;
    }
    const timer = window.setInterval(() => {
      void fetchOverview(selectedSessionId);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [fetchOverview, progressStatus, selectedSessionId]);

  const orderedEvents = useMemo(() => {
    return [...events]
      .filter((event) => isRemediationTimelineEvent(event))
      .sort((left, right) => left.timestamp.localeCompare(right.timestamp));
  }, [events]);

  const planSteps = overview?.plan.steps ?? [];

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          description="展示修复计划、审批状态、执行进度、观察结果与最终结论。"
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
        description="先选择会话，再查看该会话的执行步骤、审批/执行/观察时间线和最终结果。"
        title="会话选择"
        variant="soft"
      >
        <div className="input-row">
          <Select
            className="app-select"
            disabled={isLoadingSessions || sessionOptions.length === 0}
            loading={isLoadingSessions}
            onChange={(value) => {
              const nextId = String(value ?? "").trim();
              setSelectedSessionId(nextId);
              setSessionId(nextId);
              navigate(`/remediation/${nextId}`);
              void fetchOverview(nextId);
            }}
            options={sessionOptions.map((session) => ({
              label: sessionOptionLabel(session),
              value: session.session_id,
            }))}
            placeholder="请选择要查看的修复会话"
            value={selectedSessionId || undefined}
          />
          <AppButton
            disabled={!selectedSessionId}
            iconLeft="refresh"
            onClick={() => {
              if (selectedSessionId) {
                void fetchOverview(selectedSessionId);
              }
            }}
            variant="secondary"
          >
            刷新
          </AppButton>
        </div>
        {sessionLoadError ? <p className="data-list__copy">{sessionLoadError}</p> : null}
      </SurfaceCard>

      <SurfaceCard
        actions={
          <AppButton
            disabled={!overview?.approval_required || !selectedSessionId}
            onClick={() => setApprovalDialogOpen(true)}
            variant="primary"
          >
            打开审批窗口
          </AppButton>
        }
        description="当前修复计划步骤与验证方式。"
        title="执行步骤"
      >
        <div className="mini-card-list">
          {planSteps.length === 0 ? (
            <div className="mini-card">
              <p className="mini-card__title">当前会话暂无可执行步骤</p>
              <p className="mini-card__copy">请先在诊断阶段生成并审批修复计划。</p>
            </div>
          ) : (
            planSteps.map((step) => (
              <div key={step.step_id} className="mini-card">
                <div className="status-row">
                  <StatusChip tone="accent">步骤 {step.step_id}</StatusChip>
                  <StatusChip tone="neutral">{step.tool}</StatusChip>
                  <StatusChip tone="info">{formatVerificationMethod(step.verification.method)}</StatusChip>
                </div>
                <p className="mini-card__title">{step.description}</p>
                <p className="mini-card__copy">验证方式：{formatVerificationMethod(step.verification.method)}</p>
              </div>
            ))
          )}
        </div>
      </SurfaceCard>

      <SurfaceCard description="按修复关键事件回放：审批、执行、观察、结果、回滚。" title="修复时间线">
        <div className="mini-card-list">
          {orderedEvents.length === 0 ? (
            <div className="mini-card">
              <p className="mini-card__title">暂无修复事件</p>
              <p className="mini-card__copy">请先审批并执行修复，或切换到已有修复记录的会话。</p>
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
                    <StatusChip tone="neutral">{formatWorkflowStatus(stage, stage)}</StatusChip>
                    <StatusChip tone="info">{formatTimestamp(event.timestamp)}</StatusChip>
                  </div>
                  <p className="mini-card__title">{message}</p>
                  {event.type === "approval_required" ? (
                    <p className="mini-card__copy">待审批版本：v{String(data.plan_version ?? "-")}</p>
                  ) : null}
                  {event.type === "plan_revised" ? (
                    <p className="mini-card__copy">修订指令：{String(data.instruction ?? "-")}</p>
                  ) : null}
                  {typeof timeoutSeconds === "number" ? <p className="mini-card__copy">超时阈值：{timeoutSeconds} 秒</p> : null}
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

      <SurfaceCard description="当前未启用分批执行；这里显示一次性执行进度和最终修复结果。" title="金丝雀进度">
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
