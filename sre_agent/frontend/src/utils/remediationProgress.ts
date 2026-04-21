import type { RemediationOverview, SessionEvent } from "../api/types";

const APPROVAL_PENDING_STATUSES = new Set(["pending", "approval_required", "awaiting_approval", "approval_rejected", "rejected"]);
const APPROVAL_ACCEPTED_STATUSES = new Set(["approval_accepted", "approved"]);
const POST_APPROVAL_STATUSES = new Set([
  "execution_started",
  "execution_mocked",
  "remediating",
  "validating",
  "observation_started",
  "observation_result",
  "execution_succeeded",
  "execution_failed",
  "execution_timeout",
  "rollback_started",
  "rollback_succeeded",
  "rollback_failed",
  "resolved",
  "failed",
  "escalated",
  "timeout",
  "partially_resolved",
  "proposed_fix_ready",
  "re_diagnosed",
  "plan_revised",
]);

const OBSERVATION_FALLBACK_POLICY = "alert_status_only_when_post_metrics_unavailable";

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function getEventStage(event: SessionEvent): string {
  if (event.type !== "remediation_progress") return String(event.type ?? "").trim().toLowerCase();
  return String(event.data?.stage ?? "").trim().toLowerCase();
}

function normalizeStatus(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

function toBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function hasApprovalAccepted(overview: RemediationOverview): boolean {
  const timeline = overview.timeline ?? [];
  for (const event of timeline) {
    const stage = getEventStage(event);
    if (!stage) continue;
    if (APPROVAL_ACCEPTED_STATUSES.has(stage) || POST_APPROVAL_STATUSES.has(stage) || stage.startsWith("canary_")) {
      return true;
    }
  }

  const status = normalizeStatus(overview.progress.status);
  if (!status) return false;
  if (APPROVAL_PENDING_STATUSES.has(status)) return false;
  if (APPROVAL_ACCEPTED_STATUSES.has(status) || POST_APPROVAL_STATUSES.has(status) || status.startsWith("canary_")) {
    return true;
  }
  return false;
}

function getLatestObservationEventData(timeline: SessionEvent[]): Record<string, unknown> | null {
  const sorted = [...timeline].sort((left, right) => right.timestamp.localeCompare(left.timestamp));
  for (const event of sorted) {
    const stage = getEventStage(event);
    if (event.type === "observation_result" || stage === "observation_result") {
      return event.data ?? {};
    }
  }
  return null;
}

function getObservationPassed(timeline: SessionEvent[]): boolean | null {
  const data = getLatestObservationEventData(timeline);
  if (!data) return null;

  const alertCleared = toBoolean(data.alert_cleared);
  const metricsImproved = toBoolean(data.metrics_improved);
  if (alertCleared !== null && metricsImproved !== null) {
    return alertCleared && metricsImproved;
  }

  const policyApplied = normalizeStatus(data.policy_applied);
  if (policyApplied === OBSERVATION_FALLBACK_POLICY && alertCleared !== null) {
    return alertCleared;
  }

  return false;
}

export function getRemediationStepProgress(overview?: RemediationOverview): number {
  if (!overview) return 0;
  const total = Number(overview.progress.total_steps ?? 0);
  const completed = Number(overview.progress.completed_steps ?? 0);
  if (total <= 0) return 0;
  return clamp(Math.round((completed / total) * 100), 0, 100);
}

export function getRemediationOverallProgressDisplay(overview?: RemediationOverview): number {
  if (!overview) return 0;
  if (!hasApprovalAccepted(overview)) return 0;

  const totalSteps = Number(overview.progress.total_steps ?? 0);
  const completedSteps = Number(overview.progress.completed_steps ?? 0);
  const executionRatio = totalSteps > 0 ? clamp(completedSteps / totalSteps, 0, 1) : 0;

  let progress = 10 + 70 * executionRatio;
  const observationPassed = getObservationPassed(overview.timeline ?? []);
  if (observationPassed === true) {
    progress += 20;
  }

  return clamp(Math.round(progress), 0, 100);
}
