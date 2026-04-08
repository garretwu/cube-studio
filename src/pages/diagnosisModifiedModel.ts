import type { ChatMessage, DiagnosisResult, DiagnosisSession, Observation, ThinkingStep } from "../api/types";

type ChipTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

type TimelineToolStatus = "loading" | "success" | "error";

type TimelineSortItem = {
  order: number;
  timestamp: string;
  item: DiagnosisModifiedTimelineItem;
};

export type DiagnosisModifiedTimelineItem =
  | {
      id: string;
      kind: "message";
      role: "user" | "assistant";
      content: string;
      timestamp: string;
      label?: string;
    }
  | {
      id: string;
      kind: "thinking";
      title: string;
      content: string;
      timestamp: string;
      toolName?: string | null;
      status: "thinking" | "completed";
    }
  | {
      id: string;
      kind: "tool";
      toolName: string;
      params: Record<string, unknown>;
      timestamp: string;
      status: TimelineToolStatus;
      summaryLines: string[];
      rawResult?: Record<string, unknown> | null;
    };

export type DiagnosisModifiedCandidateView = {
  id: string;
  title: string;
  summary: string;
  confidence: number;
  confidenceLabel: string;
  statusLabel: string;
  statusTone: ChipTone;
  evidenceFor: string[];
  evidenceAgainst: string[];
  layer?: string;
  entities: string[];
  isPrimary: boolean;
};

export type DiagnosisModifiedSummaryView = {
  title: string;
  subtitle: string;
  certaintyLabel: string;
  certaintyTone: ChipTone;
  confidenceLabel: string;
  priorityLabel?: string;
  sessionLabel?: string;
  affectedServices: string[];
  impactSummary: string;
};

export type DiagnosisModifiedPlanStepView = {
  id: string;
  title: string;
  detail: string;
  toolName?: string;
  paramsSummary?: string;
  status: "done" | "pending";
};

export type DiagnosisModifiedPlanView = {
  title: string;
  description: string;
  priorityLabel: string;
  confidenceLabel: string;
  safetyLabel?: string;
  canaryLabel?: string;
  steps: DiagnosisModifiedPlanStepView[];
};

export type DiagnosisModifiedLiveView = {
  timeline: DiagnosisModifiedTimelineItem[];
  candidates: DiagnosisModifiedCandidateView[];
  summary?: DiagnosisModifiedSummaryView;
  plan?: DiagnosisModifiedPlanView;
};

export type DiagnosisModifiedDemoEvent =
  | {
      delayMs: number;
      type: "append";
      item: DiagnosisModifiedTimelineItem;
    }
  | {
      delayMs: number;
      type: "update_tool";
      targetId: string;
      summaryLines: string[];
      rawResult?: Record<string, unknown>;
    }
  | {
      delayMs: number;
      type: "update_thinking";
      targetId: string;
      status: "thinking" | "completed";
    }
  | {
      delayMs: number;
      type: "complete";
    };

export type DiagnosisModifiedDemoScenario = {
  initialTimeline: DiagnosisModifiedTimelineItem[];
  events: DiagnosisModifiedDemoEvent[];
  candidates: DiagnosisModifiedCandidateView[];
  summary: DiagnosisModifiedSummaryView;
  plan: DiagnosisModifiedPlanView;
};

function isThinkingStep(entry: ThinkingStep | Observation): entry is ThinkingStep {
  return "thought" in entry;
}

function formatValue(value: unknown): string {
  if (value == null) {
    return "-";
  }

  if (typeof value === "string") {
    return value.length > 72 ? `${value.slice(0, 69)}...` : value;
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  if (Array.isArray(value)) {
    return value.slice(0, 3).map((item) => formatValue(item)).join(", ");
  }

  return JSON.stringify(value);
}

function summarizeResult(result: Record<string, unknown> | undefined): string[] {
  if (!result) {
    return ["Waiting for tool result..."];
  }

  const entries = Object.entries(result)
    .filter(([, value]) => value !== undefined && value !== null)
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${formatValue(value)}`);

  return entries.length > 0 ? entries : ["Tool finished, but no displayable summary was returned."];
}

function getConfidenceLabel(value: number | undefined) {
  const safeValue = Math.max(0, Math.min(1, value ?? 0));
  return `${Math.round(safeValue * 100)}%`;
}

function getCertaintyTone(value: DiagnosisResult["diagnosis_certainty"] | undefined): ChipTone {
  switch (value) {
    case "confirmed":
      return "success";
    case "probable":
      return "accent";
    case "ambiguous":
      return "warning";
    default:
      return "neutral";
  }
}

function getCertaintyLabel(value: DiagnosisResult["diagnosis_certainty"] | undefined) {
  switch (value) {
    case "confirmed":
      return "Confirmed";
    case "probable":
      return "Likely";
    case "ambiguous":
      return "Needs more evidence";
    default:
      return "Diagnosing";
  }
}

function getHypothesisTone(status: DiagnosisResult["hypotheses"][number]["status"]): ChipTone {
  switch (status) {
    case "confirmed":
      return "success";
    case "testing":
      return "warning";
    case "eliminated":
      return "neutral";
    default:
      return "neutral";
  }
}

function getHypothesisLabel(status: DiagnosisResult["hypotheses"][number]["status"]) {
  switch (status) {
    case "confirmed":
      return "Confirmed";
    case "testing":
      return "Testing";
    case "eliminated":
      return "Eliminated";
    default:
      return "Candidate";
  }
}

function formatParamsSummary(params: Record<string, unknown>) {
  const parts = Object.entries(params)
    .slice(0, 3)
    .map(([key, value]) => `${key}=${formatValue(value)}`);
  return parts.join(" | ");
}

function buildSummary(session: DiagnosisSession | undefined): DiagnosisModifiedSummaryView | undefined {
  const result = session?.diagnosis_result;
  if (!result) {
    return session
      ? {
          title: session.alert.alert_name,
          subtitle: "The session has not produced a final diagnosis result yet.",
          certaintyLabel: "Diagnosing",
          certaintyTone: "info",
          confidenceLabel: "--",
          priorityLabel: undefined,
          sessionLabel: session.session_id,
          affectedServices: [],
          impactSummary: "Waiting for more thinking traces and tool observations.",
        }
      : undefined;
  }

  return {
    title: result.root_cause,
    subtitle: result.root_cause_layer || "Root-cause layer is still being refined.",
    certaintyLabel: getCertaintyLabel(result.diagnosis_certainty),
    certaintyTone: getCertaintyTone(result.diagnosis_certainty),
    confidenceLabel: getConfidenceLabel(result.confidence),
    priorityLabel: result.triage_priority,
    sessionLabel: session?.session_id,
    affectedServices: result.affected_services,
    impactSummary: result.impact_summary,
  };
}

function buildCandidates(session: DiagnosisSession | undefined): DiagnosisModifiedCandidateView[] {
  const result = session?.diagnosis_result;
  if (!result) {
    return [];
  }

  const items: DiagnosisModifiedCandidateView[] = [];
  const normalizedRootCause = result.root_cause.trim();

  if (normalizedRootCause) {
    items.push({
      id: `primary-${normalizedRootCause}`,
      title: normalizedRootCause,
      summary: "This is currently the highest-priority candidate from the diagnosis process.",
      confidence: result.confidence,
      confidenceLabel: getConfidenceLabel(result.confidence),
      statusLabel: getCertaintyLabel(result.diagnosis_certainty),
      statusTone: getCertaintyTone(result.diagnosis_certainty),
      evidenceFor: result.hypotheses[0]?.evidence_for?.slice(0, 3) ?? [result.impact_summary],
      evidenceAgainst: result.hypotheses[0]?.evidence_against?.slice(0, 2) ?? [],
      layer: result.root_cause_layer,
      entities: result.root_cause_entities,
      isPrimary: true,
    });
  }

  result.hypotheses.forEach((hypothesis, index) => {
    if (hypothesis.description.trim() === normalizedRootCause) {
      return;
    }

    items.push({
      id: `hypothesis-${index + 1}`,
      title: hypothesis.description,
      summary: hypothesis.status === "eliminated" ? "Current evidence does not support this path." : "This path remains in the active candidate set.",
      confidence: hypothesis.confidence,
      confidenceLabel: getConfidenceLabel(hypothesis.confidence),
      statusLabel: getHypothesisLabel(hypothesis.status),
      statusTone: getHypothesisTone(hypothesis.status),
      evidenceFor: hypothesis.evidence_for.slice(0, 3),
      evidenceAgainst: hypothesis.evidence_against.slice(0, 2),
      layer: index === 0 ? result.root_cause_layer : undefined,
      entities: index === 0 ? result.root_cause_entities : [],
      isPrimary: false,
    });
  });

  return items;
}

function buildPlan(session: DiagnosisSession | undefined): DiagnosisModifiedPlanView | undefined {
  const plan = session?.diagnosis_result?.recommended_fix;
  if (!plan) {
    return undefined;
  }

  const isResolved = ["resolved", "closed"].includes(session?.status ?? "");

  return {
    title: plan.root_cause,
    description: plan.description,
    priorityLabel: plan.priority,
    confidenceLabel: getConfidenceLabel(plan.confidence),
    safetyLabel: plan.safety_level,
    canaryLabel:
      plan.canary?.enabled
        ? `Canary ${plan.canary.target_percentage}% | Observe ${plan.canary.monitor_duration}m`
        : undefined,
    steps: plan.steps.map((step, index) => ({
      id: `${plan.plan_id}-${step.step_id}-${index}`,
      title: step.description,
      detail: `${step.tool} | timeout ${step.timeout}s | verify ${step.verification.method}`,
      toolName: step.tool,
      paramsSummary: Object.keys(step.params).length > 0 ? formatParamsSummary(step.params) : undefined,
      status: isResolved && index === 0 ? "done" : "pending",
    })),
  };
}

export function buildDiagnosisModifiedLiveView(
  session: DiagnosisSession | undefined,
  messages: ChatMessage[],
): DiagnosisModifiedLiveView {
  const timelineItems: TimelineSortItem[] = [];
  const traceEntries = session?.trace?.steps ?? [];

  for (let index = 0; index < traceEntries.length; index += 1) {
    const entry = traceEntries[index];
    if (!entry) {
      continue;
    }

    if (isThinkingStep(entry)) {
      timelineItems.push({
        order: timelineItems.length,
        timestamp: entry.timestamp,
        item: {
          id: `trace-thinking-${index + 1}-${entry.timestamp}`,
          kind: "thinking",
          title:
            entry.action_type === "tool_call"
              ? "Agent is planning a tool call"
              : entry.action_type === "conclude"
                ? "Agent is converging on the diagnosis"
                : "Agent is expanding diagnostic context",
          content: entry.thought,
          timestamp: entry.timestamp,
          toolName: entry.tool_name,
          status: "completed",
        },
      });

      if (entry.action_type === "tool_call" && entry.tool_name) {
        const nextEntry = traceEntries[index + 1];
        const nextObservation = nextEntry && !isThinkingStep(nextEntry) ? nextEntry : undefined;
        const isMatchedObservation = nextObservation?.tool === entry.tool_name;

        timelineItems.push({
          order: timelineItems.length,
          timestamp: nextObservation?.timestamp ?? entry.timestamp,
          item: {
            id: `trace-tool-${index + 1}-${entry.timestamp}`,
            kind: "tool",
            toolName: entry.tool_name,
            params: entry.tool_params ?? {},
            timestamp: nextObservation?.timestamp ?? entry.timestamp,
            status: isMatchedObservation ? "success" : "loading",
            summaryLines: summarizeResult(nextObservation?.result),
            rawResult: nextObservation?.result,
          },
        });

        if (isMatchedObservation) {
          index += 1;
        }
      }

      continue;
    }

    timelineItems.push({
      order: timelineItems.length,
      timestamp: entry.timestamp,
      item: {
        id: `trace-orphan-tool-${index + 1}-${entry.timestamp}`,
        kind: "tool",
        toolName: entry.tool,
        params: entry.params,
        timestamp: entry.timestamp,
        status: "success",
        summaryLines: summarizeResult(entry.result),
        rawResult: entry.result,
      },
    });
  }

  messages.forEach((message, index) => {
    if (message.role === "assistant" && message.display?.thinking_raw) {
      timelineItems.push({
        order: timelineItems.length,
        timestamp: message.created_at,
        item: {
          id: `chat-thinking-${message.id}`,
          kind: "thinking",
          title: "Pre-answer reasoning",
          content: message.display.thinking_raw,
          timestamp: message.created_at,
          status: "completed",
        },
      });
    }

    if (message.role === "tool") {
      timelineItems.push({
        order: timelineItems.length,
        timestamp: message.created_at,
        item: {
          id: `chat-tool-${message.id}`,
          kind: "tool",
          toolName: message.tool_name ?? "tool_call",
          params: {},
          timestamp: message.created_at,
          status: "success",
          summaryLines: [message.content],
        },
      });
      return;
    }

    timelineItems.push({
      order: timelineItems.length,
      timestamp: message.created_at,
      item: {
        id: `chat-message-${message.id}-${index}`,
        kind: "message",
        role: message.role === "user" ? "user" : "assistant",
        content: message.display?.answer ?? message.content,
        timestamp: message.created_at,
        label: message.role === "user" ? "User input" : "Agent response",
      },
    });
  });

  const sortedTimeline = [...timelineItems]
    .sort((left, right) => {
      const timeGap = new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
      if (timeGap !== 0) {
        return timeGap;
      }
      return left.order - right.order;
    })
    .map((entry) => entry.item);

  return {
    timeline: sortedTimeline,
    candidates: buildCandidates(session),
    summary: buildSummary(session),
    plan: buildPlan(session),
  };
}

function extractServiceName(prompt: string) {
  const matched = /([a-z0-9-]+(?:svc|service))/i.exec(prompt);
  return matched?.[1] ?? "auth-svc";
}

export function buildDiagnosisModifiedDemoScenario(prompt: string): DiagnosisModifiedDemoScenario {
  const now = Date.now();
  const serviceName = extractServiceName(prompt);
  const thinkingOneId = `demo-thinking-1-${now}`;
  const thinkingTwoId = `demo-thinking-2-${now}`;
  const thinkingThreeId = `demo-thinking-3-${now}`;
  const toolOneId = `demo-tool-metrics-${now}`;
  const toolTwoId = `demo-tool-logs-${now}`;
  const toolThreeId = `demo-tool-deploy-${now}`;
  const impactedEntities = [serviceName, `${serviceName}-cache`, `${serviceName}-db-proxy`];

  return {
    initialTimeline: [
      {
        id: `demo-user-${now}`,
        kind: "message",
        role: "user",
        content: prompt,
        timestamp: new Date(now).toISOString(),
        label: "User input",
      },
    ],
    events: [
      {
        delayMs: 240,
        type: "append",
        item: {
          id: thinkingOneId,
          kind: "thinking",
          title: "Agent is understanding the request",
          content: `The user wants to understand whether the ${serviceName} issue is caused by resource contention, cache degradation, or recent changes. First, confirm metric trends and then compare error logs.`,
          timestamp: new Date(now + 240).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 560,
        type: "append",
        item: {
          id: toolOneId,
          kind: "tool",
          toolName: "query_service_metrics",
          params: {
            service: serviceName,
            window: "-60m",
            focus: ["latency_p95", "error_rate", "cpu_usage"],
          },
          timestamp: new Date(now + 560).toISOString(),
          status: "loading",
          summaryLines: ["Reading last 60 minutes of service metrics..."],
        },
      },
      {
        delayMs: 920,
        type: "update_tool",
        targetId: toolOneId,
        summaryLines: [
          "latency_p95: 1.8s -> 5.6s",
          "error_rate: 0.4% -> 8.2%",
          "cpu_usage: 43% (not saturated)",
          "cache_hit_ratio: 97% -> 62%",
        ],
        rawResult: {
          latency_p95: "5.6s",
          error_rate: "8.2%",
          cpu_usage: "43%",
          cache_hit_ratio: "62%",
        },
      },
      {
        delayMs: 1120,
        type: "update_thinking",
        targetId: thinkingOneId,
        status: "completed",
      },
      {
        delayMs: 1260,
        type: "append",
        item: {
          id: thinkingTwoId,
          kind: "thinking",
          title: "Agent is narrowing scope",
          content: `CPU is not saturated, but both latency and error rate are climbing while cache hit ratio drops sharply. This strongly suggests cache degradation. Next step is to verify Redis timeout or fallback failures in logs.`,
          timestamp: new Date(now + 1260).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 1500,
        type: "append",
        item: {
          id: toolTwoId,
          kind: "tool",
          toolName: "fetch_error_logs",
          params: {
            service: serviceName,
            level: "error",
            limit: 100,
          },
          timestamp: new Date(now + 1500).toISOString(),
          status: "loading",
          summaryLines: ["Fetching recent error logs..."],
        },
      },
      {
        delayMs: 1860,
        type: "update_tool",
        targetId: toolTwoId,
        summaryLines: [
          "RedisConnectionTimeoutException: timeout after 2000ms",
          "Fallback disabled by feature flag: cache_fail_open=false",
          "database_pool_wait_ms: sustained > 900ms",
        ],
        rawResult: {
          redis_timeout: true,
          cache_fail_open: false,
          database_pool_wait_ms: 930,
        },
      },
      {
        delayMs: 2060,
        type: "update_thinking",
        targetId: thinkingTwoId,
        status: "completed",
      },
      {
        delayMs: 2180,
        type: "append",
        item: {
          id: `demo-assistant-stage-${now}`,
          kind: "message",
          role: "assistant",
          content: `Current evidence suggests cache-path degradation in ${serviceName}, which is raising database wait time. I will check the latest deployment to confirm if a recent change triggered this chain.`,
          timestamp: new Date(now + 2180).toISOString(),
          label: "Stage conclusion",
        },
      },
      {
        delayMs: 2460,
        type: "append",
        item: {
          id: toolThreeId,
          kind: "tool",
          toolName: "get_recent_deployments",
          params: {
            service: serviceName,
            limit: 1,
          },
          timestamp: new Date(now + 2460).toISOString(),
          status: "loading",
          summaryLines: ["Reading latest deployment record..."],
        },
      },
      {
        delayMs: 2820,
        type: "update_tool",
        targetId: toolThreeId,
        summaryLines: [
          "deploy_version: v1.4.2",
          "deployed_at: 15 minutes ago",
          "change_note: disable cache fail-open fallback",
        ],
        rawResult: {
          deploy_version: "v1.4.2",
          deployed_at: "15 minutes ago",
          change_note: "disable cache fail-open fallback",
        },
      },
      {
        delayMs: 3220,
        type: "append",
        item: {
          id: thinkingThreeId,
          kind: "thinking",
          title: "Agent is converging root cause",
          content: `The timeline is now consistent: cache hit ratio dropped, Redis timed out, database wait increased, and the latest deployment disabled fail-open fallback. Most likely the removed protection caused traffic to penetrate into the database queue.`,
          timestamp: new Date(now + 3220).toISOString(),
          status: "thinking",
        },
      },
      {
        delayMs: 3460,
        type: "update_thinking",
        targetId: thinkingThreeId,
        status: "completed",
      },
      {
        delayMs: 3560,
        type: "append",
        item: {
          id: `demo-assistant-final-${now}`,
          kind: "message",
          role: "assistant",
          content: `The issue is now converged to cache degradation: ${serviceName} disabled cache fail-open in a recent deployment, so Redis timeouts leaked requests into the database and amplified user-facing errors. RCA and approval-ready plan are shown below.`,
          timestamp: new Date(now + 3560).toISOString(),
          label: "Final conclusion",
        },
      },
      {
        delayMs: 3860,
        type: "complete",
      },
    ],
    candidates: [
      {
        id: `demo-candidate-primary-${now}`,
        title: "Cache fail-open protection was disabled, causing database penetration",
        summary: "This is the strongest explanation based on the timeline and evidence chain.",
        confidence: 0.82,
        confidenceLabel: "82%",
        statusLabel: "Likely",
        statusTone: "accent",
        evidenceFor: [
          "Cache hit ratio dropped from 97% to 62%",
          "RedisConnectionTimeoutException appears in logs",
          "Latest deployment explicitly disabled cache_fail_open",
        ],
        evidenceAgainst: ["CPU is not saturated, so this is not pure compute hotspot"],
        layer: "service",
        entities: impactedEntities,
        isPrimary: true,
      },
      {
        id: `demo-candidate-secondary-${now}`,
        title: "Redis instance performance jitter amplified downstream impact",
        summary: "Still possible, but current evidence cannot independently explain database queue inflation.",
        confidence: 0.61,
        confidenceLabel: "61%",
        statusLabel: "Testing",
        statusTone: "warning",
        evidenceFor: ["Redis timeout logs are concentrated", "Cache hit ratio dropped in the same window"],
        evidenceAgainst: ["No direct Redis node resource anomaly confirmed yet"],
        layer: "service",
        entities: [`${serviceName}-cache`],
        isPrimary: false,
      },
      {
        id: `demo-candidate-third-${now}`,
        title: "Database pool sizing is too small and magnifies wait time",
        summary: "Explains symptoms, but likely a secondary effect triggered by cache degradation.",
        confidence: 0.44,
        confidenceLabel: "44%",
        statusLabel: "Needs more evidence",
        statusTone: "neutral",
        evidenceFor: ["database_pool_wait_ms keeps increasing"],
        evidenceAgainst: ["Change history first points to cache degradation logic"],
        layer: "platform",
        entities: [`${serviceName}-db-proxy`],
        isPrimary: false,
      },
    ],
    summary: {
      title: "Cache degradation amplified database queue wait",
      subtitle: `${serviceName} issue converged to a missing protection path in cache fallback behavior.`,
      certaintyLabel: "Likely",
      certaintyTone: "accent",
      confidenceLabel: "82%",
      priorityLabel: "P1",
      sessionLabel: undefined,
      affectedServices: [serviceName],
      impactSummary: `Impact is concentrated on ${serviceName} and its cache/database-proxy chain. No clear cross-service spread has been observed.`,
    },
    plan: {
      title: "Missing cache protection caused DB penetration",
      description: "Restore cache protection with minimal blast radius, validate queue recovery in a canary slice, and expand only after metrics stabilize.",
      priorityLabel: "P1",
      confidenceLabel: "82%",
      safetyLabel: "Approval required",
      canaryLabel: "Canary 10% | Observe 15m",
      steps: [
        {
          id: `demo-plan-1-${now}`,
          title: `Re-enable cache fail-open for ${serviceName}`,
          detail: "feature_flag.update | timeout 30s | verify tool_call",
          toolName: "update_feature_flag",
          paramsSummary: `service=${serviceName} | cache_fail_open=true`,
          status: "pending",
        },
        {
          id: `demo-plan-2-${now}`,
          title: `Restart only canary ${serviceName} pods and monitor Redis timeout + DB wait`,
          detail: "rollout.restart | timeout 180s | verify promql",
          toolName: "rollout_restart",
          paramsSummary: `service=${serviceName} | scope=canary`,
          status: "pending",
        },
        {
          id: `demo-plan-3-${now}`,
          title: "Promote to full rollout after recovery metrics are confirmed",
          detail: "rollout.promote | timeout 300s | verify wait",
          toolName: "rollout_promote",
          paramsSummary: `service=${serviceName} | batch=all`,
          status: "pending",
        },
      ],
    },
  };
}
