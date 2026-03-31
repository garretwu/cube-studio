import type { DiagnosisSession } from "../api/types";
import { formatTimestamp } from "../utils/format";
import { formatWorkflowStatus } from "../utils/display";
import { StatusChip } from "./ui";

type LifecycleVisualState = "completed" | "current" | "pending" | "skipped" | "failed";

type LifecycleStep = {
  key: string;
  label: string;
  detail: string;
  state: LifecycleVisualState;
  badge: string;
};

function getStateTone(state: LifecycleVisualState) {
  switch (state) {
    case "completed":
      return "success";
    case "current":
      return "accent";
    case "failed":
      return "danger";
    case "skipped":
      return "info";
    default:
      return "neutral";
  }
}

function getStateLabel(state: LifecycleVisualState) {
  switch (state) {
    case "completed":
      return "已完成";
    case "current":
      return "当前阶段";
    case "failed":
      return "异常结束";
    case "skipped":
      return "未触发";
    default:
      return "待触发";
  }
}

function buildLifecycleSteps(session?: DiagnosisSession): LifecycleStep[] {
  const status = session?.status;
  const isTerminal = Boolean(status && ["resolved", "failed", "escalated", "timeout"].includes(status));
  const hasRediagnosis = Boolean((session?.re_diagnosis_round ?? 0) > 0 || status === "re_diagnosed");
  const isFailureTerminal = status === "failed" || status === "timeout" || status === "escalated";

  return [
    {
      key: "ingest",
      label: "告警接入",
      detail: session ? `${session.alert.alert_name} · ${formatTimestamp(session.alert.starts_at)}` : "等待诊断对象进入生命周期",
      state: session ? "completed" : "current",
      badge: session ? "诊断已启动" : "等待对象",
    },
    {
      key: "diagnosing",
      label: "诊断推理",
      detail: session?.trace?.steps?.length
        ? `已累计 ${session.trace.steps.length} 条推理轨迹`
        : "等待推理轨迹和观察结果进入会话",
      state: !session ? "pending" : status === "diagnosing" ? "current" : "completed",
      badge: !session ? "等待推理" : status === "diagnosing" ? formatWorkflowStatus(status) : "推理完成",
    },
    {
      key: "diagnosed",
      label: "根因确认",
      detail: session?.diagnosis_result?.root_cause ?? "等待输出根因和影响摘要",
      state: !session
        ? "pending"
        : status === "diagnosing"
          ? "pending"
          : status === "diagnosed"
            ? "current"
            : "completed",
      badge: session?.diagnosis_result ? formatWorkflowStatus(session.diagnosis_result.diagnosis_certainty) : "等待结论",
    },
    {
      key: "remediating",
      label: "修复验证",
      detail:
        session?.status === "remediating"
          ? "正在执行修复动作并等待验证结果"
          : session?.outcome === "proposed_fix_ready"
            ? "修复方案已生成，可进入审批或执行"
            : "等待进入修复阶段",
      state: !session
        ? "pending"
        : status === "remediating"
          ? "current"
          : status === "re_diagnosed" || isTerminal
            ? "completed"
            : status === "diagnosed" && session.outcome === "proposed_fix_ready"
              ? "completed"
              : "pending",
      badge: session?.outcome ? formatWorkflowStatus(session.outcome) : "等待修复",
    },
    {
      key: "rediagnosis",
      label: "复诊回环",
      detail: hasRediagnosis
        ? `已触发 ${session?.re_diagnosis_round ?? 1} 轮复诊，回看失败候选与新增证据`
        : "本次会话未触发复诊回环",
      state: !session
        ? "pending"
        : status === "re_diagnosed"
          ? "current"
          : hasRediagnosis
            ? "completed"
            : "skipped",
      badge: hasRediagnosis ? formatWorkflowStatus("re_diagnosed") : "未触发",
    },
    {
      key: "terminal",
      label: "结束态",
      detail: session
        ? `当前结束态由 diagnosis.status = ${status} 驱动`
        : "等待会话进入终态",
      state: !session ? "pending" : isTerminal ? (isFailureTerminal ? "failed" : "current") : "pending",
      badge: !session ? "等待结束" : isTerminal ? formatWorkflowStatus(status) : "进行中",
    },
  ];
}

type DiagnosisExecutionFlowProps = {
  session?: DiagnosisSession;
};

function DiagnosisExecutionFlow({ session }: DiagnosisExecutionFlowProps) {
  const steps = buildLifecycleSteps(session);

  return (
    <div className="diagnosis-lifecycle">
      {steps.map((step, index) => (
        <article
          key={step.key}
          className={`diagnosis-lifecycle__step diagnosis-lifecycle__step--${step.state}`}
        >
          <div className="diagnosis-lifecycle__header">
            <StatusChip tone={getStateTone(step.state)}>
              {index + 1}. {step.label}
            </StatusChip>
            <StatusChip tone="neutral">{step.badge}</StatusChip>
          </div>
          <p className="diagnosis-lifecycle__detail">{step.detail}</p>
          <p className="diagnosis-lifecycle__state">{getStateLabel(step.state)}</p>
        </article>
      ))}
    </div>
  );
}

export default DiagnosisExecutionFlow;
