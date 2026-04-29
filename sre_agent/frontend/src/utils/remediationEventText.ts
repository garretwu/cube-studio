import type { SessionEvent } from "../api/types";

export const REMEDIATION_STAGE_LABELS: Record<string, string> = {
  approval_accepted: "审批已通过",
  approval_required: "等待审批",
  approval_rejected: "审批已拒绝",
  approved: "已批准",
  awaiting_approval: "等待审批",
  canary_batch_completed: "灰度批次完成",
  canary_batch_started: "灰度批次开始",
  canary_check_failed: "灰度验证失败",
  canary_check_passed: "灰度验证通过",
  escalated: "需要工程师介入",
  escalation_required: "需要工程师介入",
  execution_failed: "修复执行失败",
  execution_mocked: "mock 已执行修复计划",
  execution_started: "开始执行修复方案",
  execution_succeeded: "修复执行成功",
  execution_timeout: "修复执行超时",
  failed: "失败",
  next_plan_approval_required: "下一根因修复待审批",
  observation_result: "观察结果已采集",
  observation_started: "开始观察",
  partially_resolved: "部分恢复",
  pending: "待开始",
  plan_revised: "方案已修订",
  pre_remediation_baseline_collected: "已采集修复前基线",
  proposed_fix_ready: "方案待执行",
  re_diagnosed: "复诊完成",
  rejected: "已拒绝",
  remediation_progress: "修复状态更新",
  remediating: "正在执行修复步骤",
  resolved: "已恢复",
  rollback_failed: "回滚失败",
  rollback_started: "开始回滚",
  rollback_succeeded: "回滚成功",
  timeout: "超时",
  validating: "正在验证修复结果",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getPositiveNumber(value: unknown): number | null {
  const numericValue = typeof value === "string" && value.trim() ? Number(value.trim()) : value;
  if (typeof numericValue !== "number" || !Number.isFinite(numericValue) || numericValue <= 0) return null;
  return numericValue;
}

export function getRemediationStageLabel(value?: string | null, fallback = "未知"): string {
  const normalized = String(value ?? "").trim();
  if (!normalized) return fallback;
  return REMEDIATION_STAGE_LABELS[normalized] ?? normalized;
}

export function getRemediationEventStage(event: Pick<SessionEvent, "type" | "data">): string {
  if (event.type !== "remediation_progress") return String(event.type);
  const stage = String(event.data?.stage ?? "").trim();
  return stage || "remediation_progress";
}

export function normalizeRemediationMessage(message: string | undefined | null, stage?: string | null): string {
  const raw = String(message ?? "").trim();
  const stageLabel = getRemediationStageLabel(stage, stage || "修复状态更新");
  if (!raw || raw.toLowerCase() === "remediation status update") return stageLabel;

  const runningStepMatch = raw.match(/^正在执行步骤\s+(\d+)\/(\d+)\s*[:：]\s*Terminate suspect process\s+(.+?)\s+on node\s+(.+)$/i);
  if (runningStepMatch) {
    return `正在执行步骤 ${runningStepMatch[1]}/${runningStepMatch[2]}：终止可疑进程 ${runningStepMatch[3]}，位于节点 ${runningStepMatch[4]}`;
  }

  const stepDescriptionMatch = raw.match(/^Terminate suspect process\s+(.+?)\s+on node\s+(.+)$/i);
  if (stepDescriptionMatch) {
    return `终止可疑进程 ${stepDescriptionMatch[1]}，位于节点 ${stepDescriptionMatch[2]}`;
  }

  const targetAbsentMatch = raw.match(/^Target process\s+(.+?)\s+was not found;\s+treating this kill step as successful\.?$/i);
  if (targetAbsentMatch) {
    return `目标进程 ${targetAbsentMatch[1]} 未找到，视为该 kill_process 步骤成功`;
  }

  const timeoutMatch = raw.match(/^remediation execution timeout\s*\(([^)]+)\)$/i);
  if (timeoutMatch) return `修复执行超时（${timeoutMatch[1]}）`;

  const proposalMatch = raw.match(/^Proposal-only remediation for root cause #(\d+):\s*(.+)$/i);
  if (proposalMatch) return `针对 RANK #${proposalMatch[1]} 根因的待审批修复方案：${proposalMatch[2]}`;

  return raw;
}

function normalizeRemediationStepCounter(message: string, data: Record<string, unknown>): string {
  const counterMatch = message.match(/^正在执行步骤\s+(\d+)\/(\d+)([:：]\s*)/);
  if (!counterMatch) return message;
  const parsedCurrent = Number(counterMatch[1]);
  const parsedTotal = Number(counterMatch[2]);
  const detailsTotal = getPositiveNumber(data.steps_total);
  const total = detailsTotal ?? (Number.isFinite(parsedTotal) && parsedTotal > 0 ? parsedTotal : null);
  if (!total) return message;
  const detailsIndex = getPositiveNumber(data.display_step_index) ?? getPositiveNumber(data.step_index);
  const displayIndex = detailsIndex ?? Math.min(Math.max(parsedCurrent, 1), total);
  return message.replace(/^正在执行步骤\s+\d+\/\d+/, `正在执行步骤 ${displayIndex}/${total}`);
}

export function formatRemediationPlanText(value?: string | null): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  return normalizeRemediationMessage(raw, null);
}

export function formatRemediationEventMessage(event: Pick<SessionEvent, "type" | "data">): string {
  const stage = getRemediationEventStage(event);
  const data = isRecord(event.data) ? event.data : {};
  const message = typeof data.message === "string" ? data.message : "";
  return normalizeRemediationStepCounter(normalizeRemediationMessage(message, stage), data);
}

export function getRemediationStepDisplayIndex(item: unknown, fallback: number): number {
  if (!isRecord(item)) return fallback;
  for (const key of ["display_step_index", "step_index", "step_id"]) {
    const value = item[key];
    if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
  }
  return fallback;
}
