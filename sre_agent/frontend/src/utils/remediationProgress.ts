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
const TERMINAL_REMEDIATION_STATUSES = new Set([
  "resolved",
  "failed",
  "escalated",
  "timeout",
  "rejected",
  "execution_succeeded",
  "execution_failed",
  "execution_timeout",
  "rollback_succeeded",
  "rollback_failed",
]);
const ACTIVE_REMEDIATION_STATUSES = new Set([
  "approved",
  "approval_accepted",
  "execution_started",
  "execution_mocked",
  "remediating",
  "validating",
  "canary_started",
  "canary_progress",
  "canary_batch_started",
  "canary_batch_progress",
  "canary_check_passed",
  "canary_succeeded",
  "canary_completed",
  "full_rollout_started",
  "full_rollout_progress",
  "observation_started",
]);

export type RemediationBatchProgress = {
  batch: string;
  progress: number;
  status: string;
};

export type RemediationExecutionView = {
  startedAt: string | null;
  updatedAt: string | null;
  durationSeconds: number;
  completedSteps: number;
  totalSteps: number;
  overallProgress: number;
  canaryProgress: number | null;
  batchStatus: RemediationBatchProgress[];
  status: string;
  isTerminal: boolean;
};

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

function sortEvents(events: SessionEvent[]): SessionEvent[] {
  return [...events].sort((left, right) => left.timestamp.localeCompare(right.timestamp));
}

function toNumber(value: unknown): number | null {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return null;
  }
  return value;
}

function getLatestEventByStages(timeline: SessionEvent[], stages: Set<string>): SessionEvent | null {
  const sorted = sortEvents(timeline);
  for (let index = sorted.length - 1; index >= 0; index -= 1) {
    const event = sorted[index];
    if (stages.has(getEventStage(event))) {
      return event;
    }
  }
  return null;
}

function getCurrentStage(timeline: SessionEvent[], fallbackStatus: string): string {
  const sorted = sortEvents(timeline);
  for (let index = sorted.length - 1; index >= 0; index -= 1) {
    const stage = getEventStage(sorted[index]);
    if (stage) {
      return stage;
    }
  }
  return normalizeStatus(fallbackStatus);
}

function getLatestUpdatedAt(timeline: SessionEvent[], fallbackUpdatedAt?: string | null): string | null {
  const sorted = sortEvents(timeline);
  const latest = sorted.at(-1);
  return latest?.timestamp ?? fallbackUpdatedAt ?? null;
}

function getExecutionStartedAt(timeline: SessionEvent[], fallbackStartedAt?: string | null): string | null {
  const executionStarted = getLatestEventByStages(timeline, new Set(["execution_started"]));
  if (executionStarted?.timestamp) {
    return executionStarted.timestamp;
  }
  return fallbackStartedAt ?? null;
}

function getTerminalTimestamp(timeline: SessionEvent[]): string | null {
  const terminalEvent = getLatestEventByStages(timeline, TERMINAL_REMEDIATION_STATUSES);
  return terminalEvent?.timestamp ?? null;
}

function getLatestEventProgress(timeline: SessionEvent[], stages: Set<string>): number | null {
  const sorted = sortEvents(timeline);
  for (let index = sorted.length - 1; index >= 0; index -= 1) {
    const event = sorted[index];
    if (!stages.has(getEventStage(event))) {
      continue;
    }
    const progress = toNumber(event.data?.progress);
    if (progress !== null) {
      return clamp(Math.round(progress), 0, 100);
    }
  }
  return null;
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

export function deriveBatchStatusFromTimeline(overview?: RemediationOverview): RemediationBatchProgress[] {
  if (!overview?.plan.canary?.enabled) {
    return [];
  }

  const timeline = sortEvents(overview.timeline ?? []);
  const totalBatches = Math.max(
    1,
    Number(overview.plan.canary.max_batches ?? 0) || overview.progress.batch_status.length || 1,
  );
  const batchMap = new Map<string, RemediationBatchProgress>();

  for (let index = 1; index <= totalBatches; index += 1) {
    const batch = `canary-${index}`;
    batchMap.set(batch, { batch, progress: 0, status: "pending" });
  }

  const setBatch = (batchKey: string, progress: number, status: string) => {
    const normalizedBatch = batchKey.trim() || "canary-1";
    batchMap.set(normalizedBatch, {
      batch: normalizedBatch,
      progress: clamp(Math.round(progress), 0, 100),
      status,
    });
  };

  for (const event of timeline) {
    const stage = getEventStage(event);
    if (!stage.startsWith("canary_")) {
      continue;
    }

    const explicitBatch = String(event.data?.batch ?? "").trim();
    const batchIndex = toNumber(event.data?.batch_index);
    const batchTotal = Math.max(1, toNumber(event.data?.batch_total) ?? totalBatches);
    const fallbackBatch =
      explicitBatch || (batchIndex !== null && batchIndex > 0 ? `canary-${batchIndex}` : "canary-1");

    if (stage === "canary_batch_started") {
      const progress = batchIndex !== null ? ((batchIndex - 1) + 0.5) / batchTotal * 100 : 0;
      setBatch(fallbackBatch, progress, "running");
      continue;
    }

    if (stage === "canary_check_passed") {
      const completed = Math.max(0, toNumber(event.data?.batch_completed) ?? 0);
      const overallProgress = batchTotal > 0 ? (completed / batchTotal) * 100 : 0;
      setBatch(fallbackBatch, overallProgress, "validating");
      continue;
    }

    if (stage === "canary_batch_completed") {
      const progress = batchIndex !== null ? (batchIndex / batchTotal) * 100 : 100;
      setBatch(fallbackBatch, progress, "resolved");
      continue;
    }

    if (stage === "canary_check_failed") {
      const existing = batchMap.get(fallbackBatch);
      setBatch(fallbackBatch, existing?.progress ?? 0, "failed");
      continue;
    }

    if (stage === "canary_started" || stage === "canary_progress" || stage === "canary_succeeded") {
      const progress = toNumber(event.data?.progress) ?? (stage === "canary_succeeded" ? 100 : 0);
      setBatch("金丝雀", progress, stage);
    }
  }

  return Array.from(batchMap.values()).sort((left, right) => left.batch.localeCompare(right.batch));
}

function getCanaryProgress(overview?: RemediationOverview): number | null {
  if (!overview?.plan.canary?.enabled) return null;

  const timeline = overview.timeline ?? [];
  const explicitProgress = getLatestEventProgress(
    timeline,
    new Set(["canary_started", "canary_progress", "canary_succeeded"]),
  );
  if (explicitProgress !== null) {
    return explicitProgress;
  }

  const batches = deriveBatchStatusFromTimeline(overview);
  if (batches.length > 0) {
    const totalBatches = Math.max(
      1,
      Number(overview.plan.canary.max_batches ?? 0) || batches.length,
    );
    let completedBatches = 0;
    let inFlightContribution = 0;
    for (const batch of batches) {
      if (batch.status === "resolved") {
        completedBatches += 1;
        continue;
      }
      if (batch.status === "running" || batch.status === "validating") {
        inFlightContribution = Math.max(inFlightContribution, 0.5);
      }
    }
    const overall = ((completedBatches + inFlightContribution) / totalBatches) * 100;
    return clamp(Math.round(overall), 0, 100);
  }

  if (String(overview.progress.status ?? "").trim().toLowerCase() === "resolved") return 100;
  return getRemediationStepProgress(overview);
}

function getCompletedSteps(overview: RemediationOverview): { completedSteps: number; totalSteps: number } {
  const totalSteps = Math.max(0, Number(overview.progress.total_steps ?? overview.plan.steps.length ?? 0));
  let completedSteps = Math.max(0, Number(overview.progress.completed_steps ?? 0));
  const timeline = sortEvents(overview.timeline ?? []);

  for (const event of timeline) {
    if (event.type !== "remediation_progress") {
      continue;
    }
    const stage = getEventStage(event);
    const explicitCompleted = toNumber(event.data?.steps_completed);
    if (explicitCompleted !== null) {
      completedSteps = Math.max(completedSteps, explicitCompleted);
    }
    if ((stage === "execution_succeeded" || stage === "resolved") && totalSteps > 0) {
      completedSteps = totalSteps;
    }
  }

  return {
    completedSteps: clamp(completedSteps, 0, totalSteps || completedSteps),
    totalSteps,
  };
}

export function getRemediationOverallProgressDisplay(overview?: RemediationOverview): number {
  if (!overview) return 0;
  if (!hasApprovalAccepted(overview)) return 0;

  const { completedSteps, totalSteps } = getCompletedSteps(overview);
  const currentStage = getCurrentStage(overview.timeline ?? [], overview.progress.status);
  const stepProgress = totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0;
  const canaryProgress = getCanaryProgress(overview);
  const executionProgressHint = getLatestEventProgress(
    overview.timeline ?? [],
    new Set([
      "canary_started",
      "canary_progress",
      "full_rollout_started",
      "full_rollout_progress",
      "execution_progress",
    ]),
  );

  if (currentStage === "resolved") {
    return 100;
  }

  const observationPassed = getObservationPassed(overview.timeline ?? []);
  if (observationPassed === true) {
    return 100;
  }

  if (currentStage === "execution_succeeded") {
    return 100;
  }

  if (TERMINAL_REMEDIATION_STATUSES.has(currentStage)) {
    return clamp(
      Math.max(
        executionProgressHint ?? 0,
        canaryProgress ?? 0,
        stepProgress,
        completedSteps > 0 ? 20 : 10,
      ),
      0,
      99,
    );
  }

  let progress = 10;
  progress = Math.max(progress, Math.round(10 + stepProgress * 0.7));

  if (canaryProgress !== null) {
    progress = Math.max(progress, Math.round(10 + canaryProgress * 0.6));
  }

  if (executionProgressHint !== null) {
    progress = Math.max(progress, Math.round(10 + executionProgressHint * 0.6));
  }

  if (currentStage === "execution_started") {
    progress = Math.max(progress, 15);
  } else if (currentStage === "remediating") {
    progress = Math.max(progress, 25);
  } else if (currentStage === "validating" || currentStage === "canary_check_passed") {
    progress = Math.max(progress, 60);
  } else if (currentStage === "observation_started") {
    progress = Math.max(progress, 85);
  }

  return clamp(Math.round(progress), 0, 100);
}

export function deriveRemediationExecutionView(args: {
  overview?: RemediationOverview;
  now?: number;
  fallbackStartedAt?: string | null;
  fallbackUpdatedAt?: string | null;
  fallbackDurationSeconds?: number | null;
}): RemediationExecutionView {
  const overview = args.overview;
  if (!overview) {
    return {
      startedAt: args.fallbackStartedAt ?? null,
      updatedAt: args.fallbackUpdatedAt ?? null,
      durationSeconds: Math.max(0, Math.floor(args.fallbackDurationSeconds ?? 0)),
      completedSteps: 0,
      totalSteps: 0,
      overallProgress: 0,
      canaryProgress: null,
      batchStatus: [],
      status: "",
      isTerminal: false,
    };
  }

  const timeline = overview.timeline ?? [];
  const startedAt = getExecutionStartedAt(timeline, args.fallbackStartedAt);
  const updatedAt = getLatestUpdatedAt(timeline, args.fallbackUpdatedAt);
  const terminalTimestamp = getTerminalTimestamp(timeline);
  const currentStage = getCurrentStage(timeline, overview.progress.status);
  const isTerminal = TERMINAL_REMEDIATION_STATUSES.has(currentStage);
  const nowMs = args.now ?? Date.now();

  let durationSeconds = Math.max(0, Math.floor(args.fallbackDurationSeconds ?? 0));
  if (startedAt) {
    const startMs = new Date(startedAt).getTime();
    const endMs = terminalTimestamp ? new Date(terminalTimestamp).getTime() : nowMs;
    if (!Number.isNaN(startMs) && !Number.isNaN(endMs) && endMs >= startMs) {
      durationSeconds = Math.floor((endMs - startMs) / 1000);
    }
  }

  const { completedSteps, totalSteps } = getCompletedSteps(overview);
  const batchStatus = deriveBatchStatusFromTimeline(overview);

  return {
    startedAt,
    updatedAt,
    durationSeconds,
    completedSteps,
    totalSteps,
    overallProgress: getRemediationOverallProgressDisplay(overview),
    canaryProgress: getCanaryProgress(overview),
    batchStatus,
    status: currentStage,
    isTerminal: isTerminal || !ACTIVE_REMEDIATION_STATUSES.has(currentStage),
  };
}
