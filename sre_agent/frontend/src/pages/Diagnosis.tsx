import { Bubble, Sender, Think } from "@ant-design/x";
import type { BubbleItemType, BubbleListProps } from "@ant-design/x";
import { Button, Collapse, Dropdown } from "antd";
import type { MenuProps } from "antd";
import { isValidElement, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import type { DiagnosisSession, Observation, RemediationPlan, ThinkingStep, WSEvent } from "../api/types";
import { buildBackendWsUrl } from "../api/ws";
import { AppIcon, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { extractRecommendedPlan, useDiagnosisStore } from "../store/diagnosisStore";
import { formatTimestamp } from "../utils/format";

type BubbleRoleType = NonNullable<BubbleListProps["role"]>;

type BubbleMeta = {
  createdAt: string;
  toolName?: string;
};

type ToolEventPayload = {
  kind: "tool_event";
  toolName: string;
  status: "loading" | "success";
  stepLabel: string;
  toolParams?: Record<string, unknown>;
  summaryLines?: string[];
};

type ThinkingPlanItem = {
  key: string;
  title: string;
  description: string;
  toolName: string;
  checkTarget: string;
  nextToolName?: string;
  status?: "loading" | "success" | "error" | "abort";
  blink?: boolean;
};

type ThinkingPlanPayload = {
  kind: "thinking_plan";
  summary: string;
  thinkTitle: string;
  thinkingText: string;
  conclusionText: string;
  toolName: string;
  toolParams: Record<string, unknown>;
  typingStage?: "thinking" | "conclusion" | "complete";
  blink?: boolean;
  items: ThinkingPlanItem[];
};


type RankedCandidateItem = {
  rank: number;
  rootCause: string;
  rootCauseLayer: "hardware" | "network" | "os" | "platform" | "service";
  confidence: number;
  rootCauseEntities: string[];
  evidenceSummary: string;
  distinguishingVerification?: string;
  hasRecommendedFix?: boolean;
};

type RootCauseCandidatesPayload = {
  kind: "ranked_candidates";
  diagnosisCertainty: "confirmed" | "probable" | "ambiguous";
  summary: string;
  candidates: RankedCandidateItem[];
};


type ApprovalPlanPriority = "P0" | "P1" | "P2";
type ApprovalPlanSafetyLevel = "low" | "medium" | "high";

type ApprovalPlanStep = {
  stepId: number;
  description: string;
  tool: string;
  timeout: number;
  verificationMethod: "promql" | "tool_call" | "wait";
  rollbackTool?: string | null;
};

type ApprovalPlanPayload = {
  kind: "approval_plan";
  candidateRank: number;
  diagnosisCertainty: RootCauseCandidatesPayload["diagnosisCertainty"];
  planId: string;
  rootCause: string;
  description: string;
  estimatedImpact: string;
  confidence: number;
  priority: ApprovalPlanPriority;
  safetyLevel: ApprovalPlanSafetyLevel;
  canary: {
    enabled: boolean;
    targetPercentage: number;
    monitorDuration: number;
    criteriaMode: "all" | "any";
    maxBatches: number;
    autoRollbackOnRegression: boolean;
    successCriteria: string[];
  };
  steps: ApprovalPlanStep[];
  rollbackSummary: string;
};

type ChatAnswerPayload = {
  kind: "chat_answer";
  answer: string;
  thinkingRaw?: string;
};

type DisplayMessage = {
  id: string;
  role: "assistant" | "user" | "tool";
  content:
    | string
    | ToolEventPayload
    | ThinkingPlanPayload
    | RootCauseCandidatesPayload
    | ApprovalPlanPayload
    | ChatAnswerPayload;
  createdAt: string;
  toolName?: string;
};

type SessionLoopCard = {
  kind: "loop";
  id: string;
  timestamp: string;
  toolName: string;
  toolParams: Record<string, unknown>;
  resultSummary: string;
  resultPayload: Record<string, unknown>;
  conclusion?: string;
};

type SessionConclusionCard = {
  kind: "diagnosis_result";
  id: string;
  timestamp: string;
  rootCause: string;
  confidence: number;
  impactSummary: string;
};

type SessionPlanCard = {
  kind: "plan";
  id: string;
  timestamp: string;
  plan: RemediationPlan;
};

type SessionCard = SessionLoopCard | SessionConclusionCard | SessionPlanCard;

type ThinkingRoundPhase = "thinking" | "tool_call" | "observation" | "conclusion" | "complete";

type ThinkingRound = {
  roundIndex: number;
  roundId: string;
  timestamp: string;
  thinkingText: string;
  toolName: string | null;
  toolParams: Record<string, unknown>;
  observation: Observation | null;
  conclusionText: string | null;
  phase: ThinkingRoundPhase;
  isActive: boolean;
};

const PHASE_ORDER: Record<ThinkingRoundPhase, number> = {
  thinking: 0,
  tool_call: 1,
  observation: 2,
  conclusion: 3,
  complete: 4,
};

function phaseAtLeast(phase: ThinkingRoundPhase, threshold: ThinkingRoundPhase): boolean {
  return PHASE_ORDER[phase] >= PHASE_ORDER[threshold];
}

function isThinkingStep(entry: ThinkingStep | Observation): entry is ThinkingStep {
  return "thought" in entry;
}

function buildThinkingRounds(steps: Array<ThinkingStep | Observation>): ThinkingRound[] {
  if (!steps.length) {
    return [];
  }

  const rounds: ThinkingRound[] = [];
  let currentRound: ThinkingRound | null = null;

  for (const entry of steps) {
    if (isThinkingStep(entry)) {
      if (entry.action_type === "tool_call") {
        currentRound = {
          roundIndex: rounds.length,
          roundId: `round-${rounds.length}-${entry.timestamp ?? Date.now()}`,
          timestamp: entry.timestamp ?? new Date().toISOString(),
          thinkingText: entry.thought ?? "",
          toolName: entry.tool_name ?? null,
          toolParams: entry.tool_params ?? {},
          observation: null,
          conclusionText: null,
          phase: "tool_call",
          isActive: false,
        };
        rounds.push(currentRound);
        continue;
      }

      if (entry.action_type === "conclude" || entry.action_type === "remediate") {
        const conclusion = entry.thought?.trim();
        if (!conclusion) {
          continue;
        }
        // Walk backward to find a round missing a conclusion
        for (let i = rounds.length - 1; i >= 0; i--) {
          if (!rounds[i].conclusionText) {
            rounds[i].conclusionText = conclusion;
            if (rounds[i].phase === "observation" || rounds[i].phase === "tool_call") {
              rounds[i].phase = "complete";
            }
            break;
          }
        }
      }
      continue;
    }

    // Observation
    if (currentRound && !currentRound.observation) {
      currentRound.observation = entry;
      if (currentRound.phase === "tool_call") {
        currentRound.phase = "observation";
      }
    }
  }

  // Mark the last non-complete round as active
  for (let i = rounds.length - 1; i >= 0; i--) {
    if (rounds[i].phase !== "complete") {
      rounds[i].isActive = true;
      break;
    }
  }

  return rounds;
}

const DEMO_STEP_LABELS = [
  { id: "1", title: "确认影响范围" },
  { id: "2", title: "规划观测动作" },
  { id: "2.1", title: "DeepThink 流式思考" },
  { id: "3", title: "收集关键观测" },
  { id: "4", title: "输出初步诊断" },
  { id: "5", title: "展示候选根因" },
  { id: "6", title: "生成待审批方案" },
  { id: "7", title: "人工审批执行" },
  { id: "8", title: "执行受控修复" },
  { id: "9", title: "输出最终结果" },
] as const;

const STEP6_APPROVAL_PLAN: ApprovalPlanPayload = {
  kind: "approval_plan",
  candidateRank: 1,
  diagnosisCertainty: "ambiguous",
  planId: "plan-candidate-1",
  rootCause: "异常基准测试进程导致 GPU 资源争用",
  description: "先小流量排空热点分片，再终止异常 GPU 进程，最后按金丝雀恢复。",
  estimatedImpact: "仅对单节点分片进行受控操作，业务整体风险可控。",
  confidence: 0.78,
  priority: "P1",
  safetyLevel: "high",
  canary: {
    enabled: true,
    targetPercentage: 0.1,
    monitorDuration: 120,
    criteriaMode: "all",
    maxBatches: 3,
    autoRollbackOnRegression: true,
    successCriteria: ["vllm_p95_ms < 300", "gpu_utilization < 0.75"],
  },
  steps: [
    {
      stepId: 1,
      description: "先从热点节点排出 10% 金丝雀流量。",
      tool: "k8s_cordon_drain",
      timeout: 60,
      verificationMethod: "wait",
      rollbackTool: "k8s_uncordon",
    },
    {
      stepId: 2,
      description: "终止节点上的异常 GPU 进程。",
      tool: "shell_command",
      timeout: 45,
      verificationMethod: "tool_call",
      rollbackTool: null,
    },
  ],
  rollbackSummary: "步骤 1 支持自动回滚（k8s_uncordon: node-gpu-01）；步骤 2 无自动回滚，保留人工兜底。",
};

function getDemoStepDisplayLabel(index: number) {
  return DEMO_STEP_LABELS[index]?.id ?? String(index + 1);
}

function renderBubbleMeta(meta?: BubbleMeta) {
  if (!meta) {
    return null;
  }

  return (
    <div className="diagnosis-bubble__meta">
      <span>{formatTimestamp(meta.createdAt)}</span>
      {meta.toolName ? <span>{meta.toolName}</span> : null}
    </div>
  );
}

function isToolEventPayload(value: unknown): value is ToolEventPayload {
  return typeof value === "object" && value !== null && (value as ToolEventPayload).kind === "tool_event";
}

function isThinkingPlanPayload(value: unknown): value is ThinkingPlanPayload {
  return typeof value === "object" && value !== null && (value as ThinkingPlanPayload).kind === "thinking_plan";
}


function isRootCauseCandidatesPayload(value: unknown): value is RootCauseCandidatesPayload {
  return typeof value === "object" && value !== null && (value as RootCauseCandidatesPayload).kind === "ranked_candidates";
}


function isApprovalPlanPayload(value: unknown): value is ApprovalPlanPayload {
  return typeof value === "object" && value !== null && (value as ApprovalPlanPayload).kind === "approval_plan";
}

function isChatAnswerPayload(value: unknown): value is ChatAnswerPayload {
  return typeof value === "object" && value !== null && (value as ChatAnswerPayload).kind === "chat_answer";
}

function renderChatAnswerCard(payload: ChatAnswerPayload) {
  return (
    <div className="diagnosis-bubble__content">
      <div>{payload.answer}</div>
      {payload.thinkingRaw ? (
        <details className="diagnosis-chat-thinking-raw">
          <summary>原始推理（调试）</summary>
          <pre>{payload.thinkingRaw}</pre>
        </details>
      ) : null}
    </div>
  );
}

function renderPlanDetails(plan: RemediationPlan) {
  return (
    <div className="diagnosis-plan-details">
      <p className="diagnosis-chat-state-card__copy">
        根因：{plan.root_cause} | 优先级：{plan.priority} | 置信度：{Math.round((plan.confidence ?? 0) * 100)}%
      </p>
      <p className="diagnosis-chat-state-card__copy">方案说明：{plan.description}</p>
      <p className="diagnosis-chat-state-card__copy">预估影响：{plan.estimated_impact}</p>
      {plan.steps.length ? (
        <div className="diagnosis-plan-steps">
          {plan.steps.map((step) => (
            <div className="diagnosis-plan-step" key={`${plan.plan_id}-${step.step_id}`}>
              <p className="diagnosis-chat-state-card__copy">
                步骤 {step.step_id}：{step.description}
              </p>
              <p className="diagnosis-chat-state-card__copy">工具：{step.tool}</p>
              <p className="diagnosis-chat-state-card__copy">
                验证：{step.verification.method}
                {step.verification.query ? ` | query: ${step.verification.query}` : ""}
                {step.verification.tool ? ` | tool: ${step.verification.tool}` : ""}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="diagnosis-chat-state-card__copy">当前计划暂无执行步骤。</p>
      )}
    </div>
  );
}

function toJsonText(payload: unknown): string {
  try {
    return JSON.stringify(payload ?? {}, null, 2);
  } catch {
    return String(payload ?? "");
  }
}

function truncateText(value: string, maxLength = 180): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength - 3)}...`;
}

function toCompactValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    const normalized = value.replace(/\s+/g, " ").trim();
    return truncateText(normalized || "(empty)");
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    if (!value.length) {
      return "[]";
    }
    const preview = value.slice(0, 2).map((item) => toCompactValue(item)).join(", ");
    return truncateText(`[${value.length}] ${preview}${value.length > 2 ? ", ..." : ""}`);
  }
  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    return keys.length ? `{${keys.slice(0, 4).join(", ")}${keys.length > 4 ? ", ..." : ""}}` : "{}";
  }
  return truncateText(String(value));
}

function summarizePayload(payload: Record<string, unknown>, maxItems = 5): Array<{ key: string; value: string }> {
  return Object.entries(payload)
    .slice(0, maxItems)
    .map(([key, value]) => ({ key, value: toCompactValue(value) }));
}

function summarizeResultPayload(result: Record<string, unknown>): string {
  const keys = Object.keys(result);
  if (keys.length === 0) {
    return "工具已执行，未返回结构化字段。";
  }
  const preview = keys.slice(0, 4).join("、");
  return `返回字段：${preview}${keys.length > 4 ? "..." : ""}`;
}

function buildSessionCards(session: DiagnosisSession | undefined, plan: RemediationPlan | null): SessionCard[] {
  const cards: SessionCard[] = [];
  if (!session) {
    return cards;
  }
  const trace = session.trace?.steps ?? [];
  const fallbackTimestamp = trace.length > 0
    ? (trace[trace.length - 1]?.timestamp ?? new Date().toISOString())
    : new Date().toISOString();
  let pendingLoop: { toolName: string; toolParams: Record<string, unknown>; bufferedConclusion?: string } | null = null;
  let delayedConclusion: string | undefined;

  trace.forEach((entry, index) => {
    if ("thought" in entry) {
      if (entry.action_type === "tool_call") {
        pendingLoop = {
          toolName: entry.tool_name ?? "tool_call",
          toolParams: entry.tool_params ?? {},
        };
        return;
      }
      if (entry.action_type === "conclude") {
        const conclusion = entry.thought?.trim();
        if (!conclusion) {
          return;
        }
        for (let cardIndex = cards.length - 1; cardIndex >= 0; cardIndex -= 1) {
          const candidate = cards[cardIndex];
          if (candidate.kind === "loop" && !candidate.conclusion) {
            candidate.conclusion = conclusion;
            return;
          }
        }
        if (pendingLoop) {
          pendingLoop.bufferedConclusion = conclusion;
        } else {
          delayedConclusion = conclusion;
        }
      }
      return;
    }

    const loopCard: SessionLoopCard = {
      kind: "loop",
      id: `loop-${index}-${entry.timestamp ?? "now"}`,
      timestamp: entry.timestamp ?? new Date().toISOString(),
      toolName: pendingLoop?.toolName ?? entry.tool ?? "tool_result",
      toolParams: pendingLoop?.toolParams ?? entry.params ?? {},
      resultSummary: summarizeResultPayload(entry.result ?? {}),
      resultPayload: entry.result ?? {},
      conclusion: pendingLoop?.bufferedConclusion ?? delayedConclusion,
    };
    cards.push(loopCard);
    pendingLoop = null;
    delayedConclusion = undefined;
  });

  if (session.diagnosis_result) {
    cards.push({
      kind: "diagnosis_result",
      id: `diagnosis-result-${session.session_id}`,
      timestamp: fallbackTimestamp,
      rootCause: session.diagnosis_result.root_cause,
      confidence: session.diagnosis_result.confidence,
      impactSummary: session.diagnosis_result.impact_summary,
    });
  }
  if (plan) {
    cards.push({
      kind: "plan",
      id: `plan-${plan.plan_id}`,
      timestamp: fallbackTimestamp,
      plan,
    });
  }
  return cards;
}

function renderSessionCard(card: SessionCard) {
  if (card.kind === "loop") {
    const paramSummary = summarizePayload(card.toolParams);
    const resultSummary = summarizePayload(card.resultPayload);
    return (
      <div key={card.id} className="diagnosis-session-card">
        <div className="status-row">
          <StatusChip tone="accent">循环卡片</StatusChip>
          <StatusChip tone="neutral">{card.toolName}</StatusChip>
          <StatusChip tone="neutral">{formatTimestamp(card.timestamp)}</StatusChip>
        </div>
        <p className="diagnosis-chat-state-card__copy">{card.resultSummary}</p>
        <p className="diagnosis-chat-state-card__copy">工具参数摘要</p>
        {paramSummary.length ? (
          <ul className="diagnosis-session-card__summary-list">
            {paramSummary.map((item) => (
              <li key={`${card.id}-param-${item.key}`} className="diagnosis-session-card__summary-item">
                <strong>{item.key}</strong>：{item.value}
              </li>
            ))}
          </ul>
        ) : (
          <p className="diagnosis-chat-state-card__copy">无参数</p>
        )}
        <p className="diagnosis-chat-state-card__copy">工具结果摘要</p>
        {resultSummary.length ? (
          <ul className="diagnosis-session-card__summary-list">
            {resultSummary.map((item) => (
              <li key={`${card.id}-result-${item.key}`} className="diagnosis-session-card__summary-item">
                <strong>{item.key}</strong>：{item.value}
              </li>
            ))}
          </ul>
        ) : (
          <p className="diagnosis-chat-state-card__copy">无结构化结果</p>
        )}
        <Collapse
          bordered={false}
          ghost
          size="small"
          items={[
            {
              key: "params",
              label: "查看完整工具参数 JSON",
              children: <pre className="diagnosis-session-card__json">{toJsonText(card.toolParams)}</pre>,
            },
            {
              key: "result",
              label: "查看完整工具结果 JSON",
              children: <pre className="diagnosis-session-card__json">{toJsonText(card.resultPayload)}</pre>,
            },
          ]}
        />
        {card.conclusion ? <p className="diagnosis-chat-state-card__copy">该轮结论：{card.conclusion}</p> : null}
      </div>
    );
  }
  if (card.kind === "diagnosis_result") {
    return (
      <div key={card.id} className="diagnosis-session-card">
        <div className="status-row">
          <StatusChip tone="success">诊断结论</StatusChip>
          <StatusChip tone="neutral">{formatTimestamp(card.timestamp)}</StatusChip>
        </div>
        <p className="diagnosis-chat-state-card__copy">根因：{card.rootCause}</p>
        <p className="diagnosis-chat-state-card__copy">置信度：{Math.round(card.confidence * 100)}%</p>
        <p className="diagnosis-chat-state-card__copy">影响：{card.impactSummary}</p>
      </div>
    );
  }
  return (
    <div key={card.id} className="diagnosis-session-card">
      <div className="status-row">
        <StatusChip tone="warning">修复计划</StatusChip>
        <StatusChip tone="neutral">{card.plan.plan_id}</StatusChip>
      </div>
      <p className="diagnosis-chat-state-card__copy">根因：{card.plan.root_cause}</p>
      <p className="diagnosis-chat-state-card__copy">说明：{card.plan.description}</p>
      <p className="diagnosis-chat-state-card__copy">步骤数：{card.plan.steps.length}</p>
    </div>
  );
}

function getRootCauseLayerLabel(layer: RankedCandidateItem["rootCauseLayer"]) {
  switch (layer) {
    case "hardware":
      return "硬件层";
    case "network":
      return "网络层";
    case "os":
      return "系统层";
    case "platform":
      return "平台层";
    case "service":
      return "服务层";
    default:
      return "未知层";
  }
}

function getCertaintyLabel(certainty: RootCauseCandidatesPayload["diagnosisCertainty"]) {
  switch (certainty) {
    case "confirmed":
      return "已确认";
    case "probable":
      return "高概率";
    case "ambiguous":
    default:
      return "存在歧义";
  }
}


function getPriorityLabel(priority: ApprovalPlanPriority) {
  switch (priority) {
    case "P0":
      return "P0（立即处理）";
    case "P1":
      return "P1（高优先）";
    case "P2":
    default:
      return "P2（常规）";
  }
}

function getSafetyLevelLabel(level: ApprovalPlanSafetyLevel) {
  switch (level) {
    case "high":
      return "高（需严格审批）";
    case "medium":
      return "中（受控执行）";
    case "low":
    default:
      return "低（可自动化）";
  }
}

function formatRatio(value: number) {
  return `${Math.round(value * 100)}%`;
}
function formatConfidence(value: number) {
  return `${Math.round(value * 100)}%`;
}
function renderToolEventCard(payload: ToolEventPayload) {
  const isLoading = payload.status === "loading";
  const statusLabel = isLoading ? "正在查询" : "查询完成";

  return (
    <div className="diagnosis-tool-event">
      <div className="diagnosis-tool-event__header">
        <span className={`diagnosis-tool-event__name${isLoading ? " diagnosis-tool-event__name--loading" : ""}`}>
          {payload.toolName}
        </span>
        <span
          className={`diagnosis-tool-event__status diagnosis-tool-event__status--${
            isLoading ? "loading" : "success"
          }`}
        >
          {statusLabel}
        </span>
      </div>

      <p className="diagnosis-tool-event__pair">{payload.stepLabel}</p>

      <Collapse
        bordered={false}
        className="diagnosis-tool-event__collapse"
        ghost
        items={[
          {
            key: "params",
            label: "查看 tool_params",
            children: <pre className="diagnosis-tool-event__params">{JSON.stringify(payload.toolParams ?? {}, null, 2)}</pre>,
          },
        ]}
        size="small"
      />

      {payload.summaryLines?.length ? (
        <div className="diagnosis-tool-event__result">
          <p className="diagnosis-tool-event__result-title">关键观测</p>
          <ul className="diagnosis-tool-event__result-list">
            {payload.summaryLines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="diagnosis-tool-event__observation-note">
            tool_result 已写入诊断轨迹，并沉淀为一条 Observation。
          </p>
        </div>
      ) : null}
    </div>
  );
}

type StreamingBlockProps = {
  text: string;
  speed?: number;
  mode: "hidden" | "streaming" | "complete";
  streamingMode?: "simulate" | "live";
  onComplete?: () => void;
};

function StreamingBlock({ text, speed = 6, mode, streamingMode = "simulate", onComplete }: StreamingBlockProps) {
  const isLive = streamingMode === "live";
  const [visibleLength, setVisibleLength] = useState(mode === "complete" ? text.length : 0);

  useEffect(() => {
    if (mode === "hidden") {
      setVisibleLength(0);
      return;
    }

    if (mode === "complete") {
      setVisibleLength(text.length);
      return;
    }

    setVisibleLength(0);
  }, [mode, text]);

  useEffect(() => {
    if (mode !== "streaming") {
      return;
    }

    if (isLive) {
      onComplete?.();
      return;
    }

    if (visibleLength >= text.length) {
      onComplete?.();
      return;
    }

    const timer = window.setTimeout(() => {
      setVisibleLength((current) => Math.min(current + 1, text.length));
    }, speed);

    return () => window.clearTimeout(timer);
  }, [mode, onComplete, speed, text.length, visibleLength, isLive]);

  const renderedText = isLive ? text : text.slice(0, visibleLength);
  const showCursor = mode === "streaming" && (isLive || visibleLength < text.length);

  return (
    <pre className="diagnosis-thinking-plan__stream">
      {renderedText}
      {showCursor ? <span className="diagnosis-thinking-plan__cursor" aria-hidden="true" /> : null}
    </pre>
  );
}

function ThinkingPlanCard({ payload }: { payload: ThinkingPlanPayload }) {
  const [thinkingDone, setThinkingDone] = useState(payload.typingStage !== "thinking");
  const [conclusionDone, setConclusionDone] = useState(payload.typingStage === "complete");
  const showConclusion = payload.typingStage !== "thinking" && thinkingDone;
  const showToolSelection = payload.typingStage === "complete" && conclusionDone;

  useEffect(() => {
    setThinkingDone(payload.typingStage !== "thinking");
    setConclusionDone(payload.typingStage === "complete");
  }, [payload.thinkTitle, payload.thinkingText, payload.conclusionText]);

  return (
    <div className="diagnosis-thinking-plan">
      <Think
        className="diagnosis-thinking-plan__think"
        title={payload.thinkTitle}
        loading={!showToolSelection}
        blink={payload.blink}
        defaultExpanded
      >
        <div className="diagnosis-thinking-plan__think-body">
          <div className="diagnosis-thinking-plan__panel">
            <div className="diagnosis-thinking-plan__section diagnosis-thinking-plan__section--thinking">
              <div className="diagnosis-thinking-plan__section-header">
                <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--thinking">
                  <AppIcon name="algorithm" size={16} />
                </span>
                <div className="diagnosis-thinking-plan__section-copy">
                  <p className="diagnosis-thinking-plan__section-title">思考过程</p>
                  <p className="diagnosis-thinking-plan__section-description">展示当前正在组织的观测路径与判断依据。</p>
                </div>
              </div>
              <StreamingBlock
                text={payload.thinkingText}
                mode={thinkingDone ? "complete" : "streaming"}
                speed={3}
                onComplete={() => setThinkingDone(true)}
              />
            </div>

            {showConclusion ? (
              <div className="diagnosis-thinking-plan__section diagnosis-thinking-plan__section--answer">
                <div className="diagnosis-thinking-plan__section-header">
                  <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--answer">
                    <AppIcon name="documentCheck" size={16} />
                  </span>
                  <div className="diagnosis-thinking-plan__section-copy">
                    <p className="diagnosis-thinking-plan__section-title">阶段结论</p>
                    <p className="diagnosis-thinking-plan__section-description">将上一段推理收敛为当前回合的可解释判断。</p>
                  </div>
                </div>
                <StreamingBlock
                  text={payload.conclusionText}
                  mode={conclusionDone ? "complete" : "streaming"}
                  speed={4}
                  onComplete={() => setConclusionDone(true)}
                />
              </div>
            ) : null}

            {showToolSelection ? (
              <div className="diagnosis-thinking-plan__tool-call diagnosis-thinking-plan__section diagnosis-thinking-plan__section--tool">
                <div className="diagnosis-thinking-plan__section-header">
                  <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--tool">
                    <AppIcon name="clipboardTasks" size={16} />
                  </span>
                  <div className="diagnosis-thinking-plan__section-copy">
                    <p className="diagnosis-thinking-plan__section-title">下一步工具选择</p>
                    <p className="diagnosis-thinking-plan__section-description">这里只决定下一步要调用哪个 tool 以及准备传什么参数，当前阶段尚未执行。</p>
                  </div>
                </div>
                <div className="status-row diagnosis-thinking-plan__tool-chips">
                  <StatusChip tone="info">计划调用</StatusChip>
                  <StatusChip tone="warning">未执行</StatusChip>
                </div>
                <p className="diagnosis-thinking-plan__row">
                  <span className="diagnosis-thinking-plan__label">工具名</span>
                  <code className="diagnosis-thinking-plan__value">{payload.toolName}</code>
                </p>
                <p className="diagnosis-thinking-plan__row">
                  <span className="diagnosis-thinking-plan__label">说明</span>
                  本阶段仅完成 tool 选择与参数草拟，真正执行后的结果将在后续工具消息中展示。
                </p>
                <Collapse
                  bordered={false}
                  className="diagnosis-thinking-plan__collapse"
                  ghost
                  items={[
                    {
                      key: "params",
                      label: "查看计划传入参数",
                      children: (
                        <pre className="diagnosis-thinking-plan__params">
                          {JSON.stringify(payload.toolParams, null, 2)}
                        </pre>
                      ),
                    },
                  ]}
                  size="small"
                />
              </div>
            ) : null}
          </div>
        </div>
      </Think>
    </div>
  );
}
function renderThinkingPlanCard(payload: ThinkingPlanPayload) {
  return <ThinkingPlanCard payload={payload} />;
}

function ThinkingRoundCard({ round }: { round: ThinkingRound }) {
  const showToolCall = phaseAtLeast(round.phase, "tool_call") && round.toolName;
  const showObservation = phaseAtLeast(round.phase, "observation") && round.observation !== null;
  const showConclusion = round.conclusionText !== null;

  const phaseLabel: Record<ThinkingRoundPhase, string> = {
    thinking: "思考中",
    tool_call: "选择工具",
    observation: "观测中",
    conclusion: "归纳中",
    complete: "已完成",
  };

  const phaseTone: Record<ThinkingRoundPhase, "accent" | "info" | "success" | "warning" | "neutral"> = {
    thinking: "accent",
    tool_call: "info",
    observation: "warning",
    conclusion: "info",
    complete: "success",
  };

  return (
    <div className="diagnosis-thinking-loop__round">
      <div className="diagnosis-thinking-loop__round-header">
        <StatusChip tone="accent">第 {round.roundIndex + 1} 轮</StatusChip>
        <StatusChip tone={phaseTone[round.phase]}>{phaseLabel[round.phase]}</StatusChip>
        <StatusChip tone="neutral">{formatTimestamp(round.timestamp)}</StatusChip>
      </div>

      {/* Thinking section */}
      <div className="diagnosis-thinking-plan__section diagnosis-thinking-plan__section--thinking">
        <div className="diagnosis-thinking-plan__section-header">
          <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--thinking">
            <AppIcon name="algorithm" size={16} />
          </span>
          <div className="diagnosis-thinking-plan__section-copy">
            <p className="diagnosis-thinking-plan__section-title">思考过程</p>
          </div>
        </div>
        <StreamingBlock
          text={round.thinkingText}
          mode={round.isActive && round.phase === "thinking" ? "streaming" : "complete"}
          speed={3}
        />
      </div>

      {/* Tool call section */}
      {showToolCall ? (
        <div className="diagnosis-thinking-plan__section diagnosis-thinking-plan__section--tool">
          <div className="diagnosis-thinking-plan__section-header">
            <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--tool">
              <AppIcon name="clipboardTasks" size={16} />
            </span>
            <div className="diagnosis-thinking-plan__section-copy">
              <p className="diagnosis-thinking-plan__section-title">工具调用</p>
            </div>
          </div>
          <div className="status-row diagnosis-thinking-plan__tool-chips">
            <StatusChip tone="info">{round.toolName}</StatusChip>
            <StatusChip tone={phaseAtLeast(round.phase, "observation") ? "success" : "warning"}>
              {phaseAtLeast(round.phase, "observation") ? "已完成" : "执行中"}
            </StatusChip>
          </div>
          <Collapse
            bordered={false}
            className="diagnosis-thinking-plan__collapse"
            ghost
            items={[
              {
                key: "params",
                label: "查看工具参数",
                children: (
                  <pre className="diagnosis-thinking-plan__params">
                    {JSON.stringify(round.toolParams, null, 2)}
                  </pre>
                ),
              },
            ]}
            size="small"
          />
        </div>
      ) : null}

      {/* Observation section */}
      {showObservation ? (
        <div className="diagnosis-thinking-plan__section diagnosis-thinking-plan__section--answer">
          <div className="diagnosis-thinking-plan__section-header">
            <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--answer">
              <AppIcon name="documentCheck" size={16} />
            </span>
            <div className="diagnosis-thinking-plan__section-copy">
              <p className="diagnosis-thinking-plan__section-title">观测结果</p>
            </div>
          </div>
          <p className="diagnosis-chat-state-card__copy">
            {summarizeResultPayload(round.observation?.result ?? {})}
          </p>
          <Collapse
            bordered={false}
            ghost
            size="small"
            items={[
              {
                key: "result",
                label: "查看完整结果 JSON",
                children: (
                  <pre className="diagnosis-session-card__json">
                    {toJsonText(round.observation?.result ?? {})}
                  </pre>
                ),
              },
            ]}
          />
        </div>
      ) : null}

      {/* Conclusion section */}
      {showConclusion ? (
        <div className="diagnosis-thinking-plan__section diagnosis-thinking-plan__section--answer">
          <div className="diagnosis-thinking-plan__section-header">
            <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--answer">
              <AppIcon name="documentCheck" size={16} />
            </span>
            <div className="diagnosis-thinking-plan__section-copy">
              <p className="diagnosis-thinking-plan__section-title">阶段结论</p>
            </div>
          </div>
          <StreamingBlock
            text={round.conclusionText ?? ""}
            mode={round.isActive && round.phase === "conclusion" ? "streaming" : "complete"}
            speed={4}
          />
        </div>
      ) : null}
    </div>
  );
}

function ThinkingLoopVisualization({
  rounds,
  diagnosisResult,
  effectivePlan,
}: {
  rounds: ThinkingRound[];
  diagnosisResult: DiagnosisSession["diagnosis_result"] | null;
  effectivePlan: RemediationPlan | null;
}) {
  if (!rounds.length) {
    return null;
  }

  const lastCompleteIndex = rounds.reduce(
    (acc, round, i) => (round.phase === "complete" ? i : acc),
    -1,
  );

  return (
    <div className="diagnosis-thinking-loop">
      {/* Round indicator bar */}
      <div className="diagnosis-thinking-loop__indicator-bar">
        {rounds.map((round, index) => {
          const isCompleted = round.phase === "complete";
          const isActive = round.isActive;
          const stateClassName = isCompleted
            ? "completed"
            : isActive
              ? "active"
              : "pending";

          return (
            <div key={round.roundId} className="diagnosis-thinking-loop__indicator-group">
              {index > 0 ? <span className="diagnosis-thinking-loop__indicator-line" /> : null}
              <span
                className={`diagnosis-thinking-loop__indicator-dot diagnosis-thinking-loop__indicator-dot--${stateClassName}`}
                title={`第 ${index + 1} 轮`}
              >
                {isCompleted ? "✓" : index + 1}
              </span>
            </div>
          );
        })}
      </div>

      {/* Round cards with connectors */}
      {rounds.map((round, index) => (
        <div key={round.roundId}>
          <ThinkingRoundCard round={round} />
          {index < rounds.length - 1 ? (
            <div className="diagnosis-thinking-loop__connector" />
          ) : null}
        </div>
      ))}

      {/* Final diagnosis card */}
      {diagnosisResult ? (
        <>
          <div className="diagnosis-thinking-loop__connector" />
          <div className="diagnosis-session-card">
            <div className="status-row">
              <StatusChip tone="success">诊断结论</StatusChip>
              <StatusChip tone="neutral">{formatTimestamp(new Date().toISOString())}</StatusChip>
            </div>
            <p className="diagnosis-chat-state-card__copy">根因：{diagnosisResult.root_cause}</p>
            <p className="diagnosis-chat-state-card__copy">
              置信度：{Math.round((diagnosisResult.confidence ?? 0) * 100)}%
            </p>
            <p className="diagnosis-chat-state-card__copy">影响：{diagnosisResult.impact_summary}</p>
          </div>
        </>
      ) : null}

      {/* Remediation plan card */}
      {effectivePlan ? (
        <>
          <div className="diagnosis-thinking-loop__connector" />
          <div className="diagnosis-session-card">
            <div className="status-row">
              <StatusChip tone="warning">修复计划</StatusChip>
              <StatusChip tone="neutral">{effectivePlan.plan_id}</StatusChip>
            </div>
            <p className="diagnosis-chat-state-card__copy">根因：{effectivePlan.root_cause}</p>
            <p className="diagnosis-chat-state-card__copy">说明：{effectivePlan.description}</p>
            <p className="diagnosis-chat-state-card__copy">步骤数：{effectivePlan.steps.length}</p>
          </div>
        </>
      ) : null}
    </div>
  );
}

function renderRootCauseCandidatesCard(payload: RootCauseCandidatesPayload) {
  const defaultActiveKey = payload.candidates.length ? [String(payload.candidates[0].rank)] : [];

  return (
    <div className="diagnosis-rootcause">
      <div className="diagnosis-rootcause__header">
        <p className="diagnosis-rootcause__title">候选根因</p>
        <span className={`diagnosis-rootcause__certainty diagnosis-rootcause__certainty--${payload.diagnosisCertainty}`}>
          诊断确定性：{getCertaintyLabel(payload.diagnosisCertainty)}
        </span>
      </div>
      <p className="diagnosis-rootcause__summary">{payload.summary}</p>
      <Collapse
        className="diagnosis-rootcause__collapse"
        defaultActiveKey={defaultActiveKey}
        ghost
        items={payload.candidates.map((candidate) => ({
          key: String(candidate.rank),
          label: (
            <div className="diagnosis-rootcause__item-title">
              <strong>{`#${candidate.rank} ${candidate.rootCause}`}</strong>
              <span>{`置信度 ${formatConfidence(candidate.confidence)}`}</span>
            </div>
          ),
          children: (
            <div className="diagnosis-rootcause__item-body">
              <p>
                <span>根因层级：</span>
                {getRootCauseLayerLabel(candidate.rootCauseLayer)}
              </p>
              <p>
                <span>影响实体：</span>
                {candidate.rootCauseEntities.join("、") || "暂无"}
              </p>
              <p>
                <span>证据摘要：</span>
                {candidate.evidenceSummary}
              </p>
              {candidate.distinguishingVerification ? (
                <p>
                  <span>区分性验证：</span>
                  {candidate.distinguishingVerification}
                </p>
              ) : null}
              <p>
                <span>方案状态：</span>
                {candidate.hasRecommendedFix ? "可生成待审批方案" : "暂无推荐方案"}
              </p>
            </div>
          ),
        }))}
      />
    </div>
  );
}


function renderApprovalPlanCard(payload: ApprovalPlanPayload) {
  const defaultActiveKey = payload.steps.length ? [String(payload.steps[0].stepId)] : [];

  return (
    <div className="diagnosis-approval-plan">
      <div className="diagnosis-approval-plan__header">
        <p className="diagnosis-approval-plan__title">待审批方案</p>
        <div className="diagnosis-approval-plan__chips">
          <span className="diagnosis-approval-plan__chip">候选根因 #{payload.candidateRank}</span>
          <span className="diagnosis-approval-plan__chip diagnosis-approval-plan__chip--certainty">
            诊断确定性：{getCertaintyLabel(payload.diagnosisCertainty)}
          </span>
        </div>
      </div>

      <p className="diagnosis-approval-plan__summary">已基于优先级最高的候选根因生成待审批修复方案，请在审批前确认风险边界。</p>

      <div className="diagnosis-approval-plan__facts">
        <p>
          <span>方案 ID：</span>
          <code>{payload.planId}</code>
        </p>
        <p>
          <span>根因：</span>
          {payload.rootCause}
        </p>
        <p>
          <span>方案描述：</span>
          {payload.description}
        </p>
        <p>
          <span>预估影响：</span>
          {payload.estimatedImpact}
        </p>
        <p>
          <span>方案置信度：</span>
          {formatConfidence(payload.confidence)}
        </p>
        <p>
          <span>优先级：</span>
          {getPriorityLabel(payload.priority)}
        </p>
        <p>
          <span>安全等级：</span>
          {getSafetyLevelLabel(payload.safetyLevel)}
        </p>
        <p>
          <span>执行步骤：</span>
          {payload.steps.length} 步
        </p>
      </div>

      <div className="diagnosis-approval-plan__canary">
        <p className="diagnosis-approval-plan__canary-title">金丝雀边界</p>
        <p className="diagnosis-approval-plan__canary-meta">
          目标流量 {formatRatio(payload.canary.targetPercentage)} · 观察 {payload.canary.monitorDuration} 秒 · 判定模式
          {payload.canary.criteriaMode === "all" ? " 全部满足" : " 任一满足"}
        </p>
        <ul className="diagnosis-approval-plan__canary-list">
          {payload.canary.successCriteria.map((criterion) => (
            <li key={criterion}>{criterion}</li>
          ))}
        </ul>
      </div>

      <Collapse
        className="diagnosis-approval-plan__steps"
        defaultActiveKey={defaultActiveKey}
        ghost
        items={payload.steps.map((step) => ({
          key: String(step.stepId),
          label: (
            <div className="diagnosis-approval-plan__step-title">
              <strong>{`步骤 ${step.stepId}：${step.description}`}</strong>
              <span>{`验证方式 ${step.verificationMethod}`}</span>
            </div>
          ),
          children: (
            <div className="diagnosis-approval-plan__step-body">
              <p>
                <span>执行工具：</span>
                <code>{step.tool}</code>
              </p>
              <p>
                <span>超时时间：</span>
                {step.timeout} 秒
              </p>
              <p>
                <span>回滚工具：</span>
                {step.rollbackTool ?? "无自动回滚"}
              </p>
            </div>
          ),
        }))}
      />

      <p className="diagnosis-approval-plan__rollback">回滚摘要：{payload.rollbackSummary}</p>
    </div>
  );
}
function DiagnosisPage() {
  const params = useParams<{ sessionId?: string }>();
  const [draft, setDraft] = useState("");
  const [planInstruction, setPlanInstruction] = useState("");
  const [demoActiveStep, setDemoActiveStep] = useState(0);
  const [isExecutingDemoStep, setIsExecutingDemoStep] = useState(false);
  const [demoMessages, setDemoMessages] = useState<DisplayMessage[]>([]);
  const demoStepTimerRef = useRef<number | undefined>(undefined);

  const {
    session,
    activeSessionId,
    messages,
    events,
    isLoadingSession,
    bootstrapStatus,
    traceStatus,
    isSendingMessage,
    isRevisingPlan,
    isApprovingPlan,
    canApprove,
    approvalBlockReason,
    currentPlanVersion,
    latestPlanVersion,
    approvedPlanVersion,
    hasPlan,
    planMissingReason,
    effectiveReviseInstruction,
    chatContextApplied,
    chatContextMeta,
    connectionState,
    error,
    bootstrapSession,
    sendMessage,
    revisePlan,
    approvePlan,
    applyEvent,
    setConnectionState,
  } = useDiagnosisStore();

  useEffect(() => {
    void bootstrapSession(params.sessionId);
  }, [bootstrapSession, params.sessionId]);

  useEffect(
    () => () => {
      if (demoStepTimerRef.current !== undefined) {
        window.clearTimeout(demoStepTimerRef.current);
      }
    },
    [],
  );

  const websocketEnabled = import.meta.env.VITE_WS_ENABLED === "true" && Boolean(activeSessionId);

  const websocketUrl = useMemo(
    () =>
      buildBackendWsUrl(`/ws/thinking-trace/${activeSessionId ?? "pending"}`, {
        token: import.meta.env.VITE_API_TOKEN ?? "",
      }),
    [activeSessionId],
  );

  const handleRealtimeEvent = useCallback(
    (event: WSEvent) => {
      applyEvent(event);
    },
    [applyEvent],
  );

  const ws = useWebSocket(websocketUrl, handleRealtimeEvent, {
    enabled: websocketEnabled,
  });

  useEffect(() => {
    setConnectionState(ws.state);
  }, [setConnectionState, ws.state]);

  const handleSubmit = useCallback(
    (value: string) => {
      const nextDraft = value.trim();
      if (!nextDraft || !activeSessionId || isSendingMessage) {
        return;
      }

      setDraft("");
      void sendMessage(nextDraft);
    },
    [activeSessionId, isSendingMessage, sendMessage],
  );

  const runStepOneImpactScope = useCallback(() => {
    if (isExecutingDemoStep) {
      return;
    }

    const now = new Date();
    const baseId = String(now.getTime());
    const stepLabel = "Step 1 · 确认影响范围";
    const toolMessageId = `demo-step1-tool-${baseId}`;

    setIsExecutingDemoStep(true);
    setDemoMessages((prev) => [
      ...prev,
      {
        id: `demo-step1-assistant-${baseId}`,
        role: "assistant",
        content: "先确认影响半径，定位受影响节点与服务，再进入后续观测。",
        createdAt: now.toISOString(),
      },
      {
        id: toolMessageId,
        role: "assistant",
        createdAt: now.toISOString(),
        content: {
          kind: "tool_event",
          toolName: "topology.get_blast_radius",
          status: "loading",
          stepLabel,
          toolParams: {
            entry_entity: "node-gpu-01",
            service_scope: ["vllm-latency", "chat-serving"],
            depth: 2,
          },
        },
      },
    ]);

    demoStepTimerRef.current = window.setTimeout(() => {
      setDemoMessages((prev) =>
        prev.map((message) => {
          if (message.id !== toolMessageId || !isToolEventPayload(message.content)) {
            return message;
          }

          return {
            ...message,
            createdAt: new Date().toISOString(),
            content: {
              ...message.content,
              status: "success",
              summaryLines: [
                "受影响节点：node-gpu-01（局部热点）",
                "受影响服务：vllm-latency、chat-serving",
                "当前未观察到跨机房扩散信号",
              ],
            },
          };
        }),
      );

      setDemoActiveStep((prev) => Math.min(prev + 1, DEMO_STEP_LABELS.length));
      setIsExecutingDemoStep(false);
      demoStepTimerRef.current = undefined;
    }, 250);
  }, [isExecutingDemoStep]);

  const runStepTwoThinkingPlan = useCallback(() => {
    if (isExecutingDemoStep) {
      return;
    }

    const now = new Date();
    const baseId = String(now.getTime());
    const thinkingMessageId = `demo-step2-thinking-${baseId}`;

    setIsExecutingDemoStep(true);

    setDemoMessages((prev) => [
      ...prev,
      {
        id: thinkingMessageId,
        role: "assistant",
        createdAt: now.toISOString(),
        content: {
          kind: "thinking_plan",
          summary: "规划开始：先展示思考闪烁状态，再逐步吐出推理内容与结论。",
          thinkTitle: "Deep Thinking 正在规划观测动作",
          thinkingText: `The user wants me to diagnose a 'KubePodNotReady' alert with critical severity.
The topology blast radius shows no affected entities, which is unusual. Let me start
by gathering evidence about what pods might be not ready in the Kubernetes
cluster.

Let me begin by:

1. Listing pods across namespaces to find not-ready pods
2. Looking for OOMKilled or pending pods
3. Checking service status
4. Querying logs for any errors

Let me start with multiple parallel queries to understand the state of the cluster.`,
          conclusionText: `I'll start by gathering evidence about the cluster state. Let me query multiple
sources in parallel to identify the affected pods and their conditions.`,
          toolName: "k8s.list_pods",
          toolParams: { kwargs: { all_namespaces: true } },
          typingStage: "thinking",
          blink: true,
          items: [
            {
              key: "step2-plan-1",
              title: "检查 Pod 就绪状态",
              description: "先在所有命名空间里找出 NotReady 的 Pod。",
              toolName: "k8s.list_pods",
              checkTarget: "定位真实受影响的 Pod 与命名空间。",
              nextToolName: "k8s.list_events",
              status: "loading",
              blink: true,
            },
            {
              key: "step2-plan-2",
              title: "补充异常事件与重启原因",
              description: "确认是否存在 OOMKilled、Pending 或镜像拉取失败。",
              toolName: "k8s.list_events",
              checkTarget: "补齐 Pod 不就绪背后的直接异常信号。",
              nextToolName: "k8s.query_logs",
            },
            {
              key: "step2-plan-3",
              title: "并行抓取服务与日志",
              description: "把服务状态和错误日志一起纳入后续证据链。",
              toolName: "k8s.query_logs",
              checkTarget: "为 Step 3 的关键观测采集准备工具路径。",
              nextToolName: "Step 3: 收集关键观测（按规划依次执行）",
            },
          ],
        },
      },
    ]);

    const updateThinkingPlan = (updater: (payload: ThinkingPlanPayload) => ThinkingPlanPayload) => {
      setDemoMessages((prev) =>
        prev.map((message) => {
          if (message.id !== thinkingMessageId || !isThinkingPlanPayload(message.content)) {
            return message;
          }

          return {
            ...message,
            createdAt: new Date().toISOString(),
            content: updater(message.content),
          };
        }),
      );
    };

    demoStepTimerRef.current = window.setTimeout(() => {
      updateThinkingPlan((payload) => ({
        ...payload,
        summary: "思考内容已流式输出完成，开始吐出结论与首个工具调用。",
        typingStage: "conclusion",
        blink: true,
        items: payload.items.map((item) => {
          if (item.key === "step2-plan-1") {
            return { ...item, status: "success", blink: false };
          }
          if (item.key === "step2-plan-2") {
            return { ...item, status: "loading", blink: true };
          }
          return { ...item, blink: false };
        }),
      }));

      demoStepTimerRef.current = window.setTimeout(() => {
        updateThinkingPlan((payload) => ({
          ...payload,
          summary: "结论已输出，继续展示后续观测动作编排。",
          typingStage: "complete",
          blink: false,
          items: payload.items.map((item) => {
            if (item.key === "step2-plan-2") {
              return { ...item, status: "success", blink: false };
            }
            if (item.key === "step2-plan-3") {
              return { ...item, status: "loading", blink: true };
            }
            return { ...item, blink: false };
          }),
        }));

        demoStepTimerRef.current = window.setTimeout(() => {
          updateThinkingPlan((payload) => ({
            ...payload,
            summary: "规划完成：观测动作与工具调用顺序已明确，thinking 与结论输出均已结束。",
            typingStage: "complete",
            blink: false,
            items: payload.items.map((item) => ({
              ...item,
              status: "success",
              blink: false,
            })),
          }));

          setDemoActiveStep((prev) => Math.min(prev + 1, DEMO_STEP_LABELS.length));
          setIsExecutingDemoStep(false);
          demoStepTimerRef.current = undefined;
        }, 250);
      }, 450);
    }, 500);
  }, [isExecutingDemoStep]);

  
const runStepTwoPointOneDeepThink = useCallback(() => {
    if (isExecutingDemoStep) {
      return;
    }

    const now = new Date();
    const baseId = String(now.getTime());

    setIsExecutingDemoStep(true);
    setDemoMessages((prev) => [
      ...prev,
      {
        id: `demo-step21-banner-${baseId}`,
        role: "assistant",
        createdAt: now.toISOString(),
        content:
          "Step 2.1 已切换到全新 DeepThink 演示：这一段会先闪烁，再逐字输出 thinking，随后再输出结论和工具调用。",
      },
      {
        id: `demo-step21-thinking-${baseId}`,
        role: "assistant",
        createdAt: new Date(now.getTime() + 10).toISOString(),
        content: {
          kind: "thinking_plan",
          summary: "Step 2.1：这是独立于原 Step 2 的新版 DeepThink 演示卡片。",
          thinkTitle: "Step 2.1 · DeepThink Streaming Demo",
          thinkingText: `The user wants me to diagnose a 'KubePodNotReady' alert with critical severity.
The topology blast radius shows no affected entities, which is unusual. Let me start
by gathering evidence about what pods might be not ready in the Kubernetes
cluster.

Let me begin by:

1. Listing pods across namespaces to find not-ready pods
2. Looking for OOMKilled or pending pods
3. Checking service status
4. Querying logs for any errors

Let me start with multiple parallel queries to understand the state of the cluster.`,
          conclusionText: `I'll start by gathering evidence about the cluster state. Let me query multiple
sources in parallel to identify the affected pods and their conditions.`,
          toolName: "k8s.list_pods",
          toolParams: { kwargs: { all_namespaces: true } },
          typingStage: "thinking",
          blink: true,
          items: [
            {
              key: "step21-plan-1",
              title: "Step 2.1 / Thinking 闪烁",
              description: "顶部 Think 状态先进入 blink 和 loading。",
              toolName: "@ant-design/x Think",
              checkTarget: "明确告诉用户当前处于思考阶段。",
              status: "loading",
              blink: true,
            },
            {
              key: "step21-plan-2",
              title: "Step 2.1 / 流式吐字",
              description: "thinking 与 conclusion 分阶段逐字输出。",
              toolName: "StreamingBlock",
              checkTarget: "避免 thinking 一次性整段出现。",
            },
          ],
        },
      },
    ]);

    demoStepTimerRef.current = window.setTimeout(() => {
      setDemoMessages((prev) =>
        prev.map((message) => {
          if (message.id !== `demo-step21-thinking-${baseId}` || !isThinkingPlanPayload(message.content)) {
            return message;
          }

          return {
            ...message,
            createdAt: new Date().toISOString(),
            content: {
              ...message.content,
              summary: "Step 2.1：thinking 已经输出完成，开始吐出结论和工具参数。",
              typingStage: "conclusion",
              items: message.content.items.map((item) => ({
                ...item,
                status: item.key === "step21-plan-1" ? "success" : "loading",
                blink: item.key === "step21-plan-2",
              })),
            },
          };
        }),
      );

      demoStepTimerRef.current = window.setTimeout(() => {
        setDemoMessages((prev) =>
          prev.map((message) => {
            if (message.id !== `demo-step21-thinking-${baseId}` || !isThinkingPlanPayload(message.content)) {
              return message;
            }

            return {
              ...message,
              createdAt: new Date().toISOString(),
              content: {
                ...message.content,
                summary: "Step 2.1：新版 DeepThink 演示完成，现在可以继续走后续诊断步骤。",
                typingStage: "complete",
                blink: false,
                items: message.content.items.map((item) => ({
                  ...item,
                  status: "success",
                  blink: false,
                })),
              },
            };
          }),
        );

        setDemoActiveStep((prev) => Math.min(prev + 1, DEMO_STEP_LABELS.length));
        setIsExecutingDemoStep(false);
        demoStepTimerRef.current = undefined;
      }, 2400);
    }, 500);
  }, [isExecutingDemoStep]);

const runStepFiveRootCauseCandidates = useCallback(() => {
    if (isExecutingDemoStep) {
      return;
    }

    const now = new Date();
    const baseId = String(now.getTime());

    setDemoMessages((prev) => [
      ...prev,
      {
        id: `demo-step5-candidates-${baseId}`,
        role: "assistant",
        createdAt: now.toISOString(),
        content: {
          kind: "ranked_candidates",
          diagnosisCertainty: "ambiguous",
          summary: "当前诊断仍存在歧义，以下给出按证据强度排序的候选根因。",
          candidates: [
            {
              rank: 1,
              rootCause: "异常基准测试进程导致 GPU 资源争用",
              rootCauseLayer: "hardware",
              confidence: 0.62,
              rootCauseEntities: ["gpu-01", "node-gpu-01"],
              evidenceSummary: "GPU 利用率持续高位，影响集中在单节点，且已有异常 GPU 进程线索。",
              distinguishingVerification: "检查 node-gpu-01 的 GPU 进程列表，并观察终止异常进程后 p95 延迟是否回落。",
              hasRecommendedFix: true,
            },
            {
              rank: 2,
              rootCause: "推理分片负载倾斜导致单节点过载",
              rootCauseLayer: "service",
              confidence: 0.54,
              rootCauseEntities: ["svc-vllm", "node-gpu-01"],
              evidenceSummary: "单节点长期承压，但暂缺直接证明是调度策略或分片权重异常。",
              distinguishingVerification: "核查分片权重、实例负载分布以及最近变更记录。",
              hasRecommendedFix: false,
            },
            {
              rank: 3,
              rootCause: "网络侧瞬时拥塞放大了推理链路时延",
              rootCauseLayer: "network",
              confidence: 0.33,
              rootCauseEntities: ["sw-01", "node-gpu-01"],
              evidenceSummary: "网络路径不能完全排除，但当前证据不足以成为首要根因。",
              distinguishingVerification: "查看交换机队列深度、ECN 与链路丢包摘要。",
              hasRecommendedFix: false,
            },
          ],
        },
      },
    ]);

    setDemoActiveStep((prev) => Math.min(prev + 1, DEMO_STEP_LABELS.length));
  }, [isExecutingDemoStep]);

  const runStepSixGenerateApprovalPlan = useCallback(() => {
    if (isExecutingDemoStep) {
      return;
    }

    const now = new Date();
    const baseId = String(now.getTime());

    setDemoMessages((prev) => [
      ...prev,
      {
        id: `demo-step6-assistant-${baseId}`,
        role: "assistant",
        createdAt: now.toISOString(),
        content: "已基于候选根因 #1 生成待审批方案，建议先确认影响范围与回滚边界，再进入人工审批。",
      },
      {
        id: `demo-step6-approval-plan-${baseId}`,
        role: "assistant",
        createdAt: new Date(now.getTime() + 10).toISOString(),
        content: STEP6_APPROVAL_PLAN,
      },
    ]);

    setDemoActiveStep((prev) => Math.min(prev + 1, DEMO_STEP_LABELS.length));
  }, [isExecutingDemoStep]);
  const effectivePlan = useMemo(() => extractRecommendedPlan(session), [session]);
  const sessionCards = useMemo(() => buildSessionCards(session, effectivePlan), [session, effectivePlan]);
  const thinkingRounds = useMemo(
    () => buildThinkingRounds(session?.trace?.steps ?? []),
    [session?.trace?.steps],
  );
  const lastUpdatedAt = useMemo(() => {
    const lastEventAt = events.length > 0 ? events[events.length - 1]?.timestamp : undefined;
    if (lastEventAt) {
      return lastEventAt;
    }
    const traceSteps = session?.trace?.steps ?? [];
    if (traceSteps.length > 0) {
      return traceSteps[traceSteps.length - 1]?.timestamp ?? undefined;
    }
    return undefined;
  }, [events, session?.trace?.steps]);
  const timelineMessages = useMemo<DisplayMessage[]>(
    () => {
      const chatMessages: DisplayMessage[] = messages.map((message) => ({
        id: message.id,
        role: message.role,
        content:
          message.role === "assistant" && message.display?.answer
            ? {
                kind: "chat_answer",
                answer: message.display.answer,
                thinkingRaw: message.display.thinking_raw ?? undefined,
              }
            : message.content,
        createdAt: message.created_at,
        toolName: message.tool_name,
      }));

      return [...chatMessages, ...demoMessages];
    },
    [messages, demoMessages],
  );

  const bubbleItems = useMemo<BubbleItemType[]>(
    () =>
      timelineMessages.map((message) => ({
        key: message.id,
        role: message.role,
        content: message.content,
        extraInfo: {
          createdAt: message.createdAt,
          toolName: message.toolName,
        } satisfies BubbleMeta,
      })),
    [timelineMessages],
  );

  const bubbleRoles = useMemo<BubbleRoleType>(
    () => ({
      assistant: {
        placement: "start",
        variant: "shadow",
        rootClassName: "diagnosis-bubble diagnosis-bubble--assistant",
        contentRender: (content: unknown) => {
          if (isToolEventPayload(content)) {
            return renderToolEventCard(content);
          }
          if (isThinkingPlanPayload(content)) {
            return renderThinkingPlanCard(content);
          }
          if (isRootCauseCandidatesPayload(content)) {
            return renderRootCauseCandidatesCard(content);
          }
          if (isApprovalPlanPayload(content)) {
            return renderApprovalPlanCard(content);
          }
          if (isChatAnswerPayload(content)) {
            return renderChatAnswerCard(content);
          }
          if (isValidElement(content)) {
            return content;
          }
          return <div className="diagnosis-bubble__content">{String(content ?? "")}</div>;
        },
        footer: (_, info) => renderBubbleMeta(info?.extraInfo as BubbleMeta | undefined),
      },
      user: {
        placement: "end",
        variant: "filled",
        rootClassName: "diagnosis-bubble diagnosis-bubble--user",
        contentRender: (content: unknown) => {
          if (isToolEventPayload(content)) {
            return renderToolEventCard(content);
          }
          if (isThinkingPlanPayload(content)) {
            return renderThinkingPlanCard(content);
          }
          if (isRootCauseCandidatesPayload(content)) {
            return renderRootCauseCandidatesCard(content);
          }
          if (isApprovalPlanPayload(content)) {
            return renderApprovalPlanCard(content);
          }
          if (isChatAnswerPayload(content)) {
            return renderChatAnswerCard(content);
          }
          if (isValidElement(content)) {
            return content;
          }
          return <div className="diagnosis-bubble__content">{String(content ?? "")}</div>;
        },
        footer: (_, info) => renderBubbleMeta(info?.extraInfo as BubbleMeta | undefined),
      },
      tool: {
        placement: "start",
        variant: "outlined",
        shape: "corner",
        rootClassName: "diagnosis-bubble diagnosis-bubble--tool",
        contentRender: (content: unknown) => {
          if (isToolEventPayload(content)) {
            return renderToolEventCard(content);
          }
          if (isThinkingPlanPayload(content)) {
            return renderThinkingPlanCard(content);
          }
          if (isRootCauseCandidatesPayload(content)) {
            return renderRootCauseCandidatesCard(content);
          }
          if (isApprovalPlanPayload(content)) {
            return renderApprovalPlanCard(content);
          }
          if (isValidElement(content)) {
            return content;
          }
          return <pre className="diagnosis-bubble__content diagnosis-bubble__content--tool">{String(content ?? "")}</pre>;
        },
        footer: (_, info) => renderBubbleMeta(info?.extraInfo as BubbleMeta | undefined),
      },
    }),
    [],
  );

  const senderDisabled = isLoadingSession || !activeSessionId;
  const traceStepsUsed = Number(chatContextMeta?.["trace_steps_used"] ?? 0);

  const handleDemoStepClick = useCallback<NonNullable<MenuProps["onClick"]>>(
    ({ key }) => {
      const clickedStep = Number(key) - 1;
      if (!Number.isInteger(clickedStep) || clickedStep !== demoActiveStep || isExecutingDemoStep) {
        return;
      }

      if (clickedStep === 0) {
        runStepOneImpactScope();
        return;
      }

      if (clickedStep === 1) {
        runStepTwoThinkingPlan();
        return;
      }

      if (clickedStep === 2) {
        runStepTwoPointOneDeepThink();
        return;
      }

      if (clickedStep === 3) {
        setDemoActiveStep((prev) => Math.min(prev + 2, DEMO_STEP_LABELS.length));
        return;
      }

      if (clickedStep === 5) {
        runStepFiveRootCauseCandidates();
        return;
      }

      if (clickedStep === 6) {
        runStepSixGenerateApprovalPlan();
        return;
      }

      setDemoActiveStep((prev) => Math.min(prev + 1, DEMO_STEP_LABELS.length));
    },
    [
      demoActiveStep,
      isExecutingDemoStep,
      runStepOneImpactScope,
      runStepTwoThinkingPlan,
      runStepTwoPointOneDeepThink,
      runStepFiveRootCauseCandidates,
      runStepSixGenerateApprovalPlan,
    ],
  );

  const demoMenuItems = useMemo<MenuProps["items"]>(
    () =>
      DEMO_STEP_LABELS.map((step, index) => {
        const isDone = index < demoActiveStep;
        const isActive = index === demoActiveStep;
        const isActiveAndBusy = isActive && isExecutingDemoStep;
        const stateClassName = isDone ? "done" : isActive ? "active" : "locked";
        const stateText = isDone ? "已完成" : isActiveAndBusy ? "执行中..." : isActive ? "点击推进" : "未解锁";

        return {
          key: String(index + 1),
          disabled: !isActive || isActiveAndBusy,
          label: (
            <div className={`diagnosis-demo-menu__item diagnosis-demo-menu__item--${stateClassName}`}>
              <strong>{`${getDemoStepDisplayLabel(index)}、${step.title}`}</strong>
              <span>{stateText}</span>
            </div>
          ),
        };
      }),
    [demoActiveStep, isExecutingDemoStep],
  );

  const demoMenu = useMemo<MenuProps>(
    () => ({
      items: demoMenuItems,
      onClick: handleDemoStepClick,
    }),
    [demoMenuItems, handleDemoStepClick],
  );

  return (
    <div className="diagnosis-chat-page">
      <div className="diagnosis-chat-page__content">
        <div className="page-intro diagnosis-chat-page__intro">
          <SectionHeader
            title="诊断对话"
            description="会话先创建后流式推送，诊断过程按循环卡片分段展示。"
          />
          <div className="status-row">
            <StatusChip tone={connectionState === "open" ? "success" : "info"}>WebSocket {connectionState}</StatusChip>
            {activeSessionId ? <StatusChip tone="neutral">会话 {activeSessionId}</StatusChip> : null}
            <StatusChip tone="accent">状态 {session?.status ?? "unknown"}</StatusChip>
            {lastUpdatedAt ? <StatusChip tone="neutral">更新 {formatTimestamp(lastUpdatedAt)}</StatusChip> : null}
            {activeSessionId ? (
              <StatusChip tone={chatContextApplied ? "success" : "info"}>
                上下文 {chatContextApplied ? "已加载" : "待加载"}
              </StatusChip>
            ) : null}
          </div>
        </div>

        <div className="diagnosis-chat-workspace">
          <SurfaceCard bodyClassName="diagnosis-chat-shell__body" className="diagnosis-chat-shell">
            {isLoadingSession ? (
              <div className="diagnosis-chat-state-card">
                <p className="diagnosis-chat-state-card__title">正在加载会话</p>
                <p className="diagnosis-chat-state-card__copy">正在同步当前诊断会话与消息历史。</p>
              </div>
            ) : null}

            {bootstrapStatus === "error" && error ? (
              <div className="diagnosis-chat-state-card diagnosis-chat-state-card--error">
                <p className="diagnosis-chat-state-card__title">诊断请求失败</p>
                <p className="diagnosis-chat-state-card__copy">{error}</p>
              </div>
            ) : null}

            {bootstrapStatus === "ready" && error ? (
              <div className="diagnosis-chat-state-card diagnosis-chat-state-card--error">
                <p className="diagnosis-chat-state-card__title">追问发送失败</p>
                <p className="diagnosis-chat-state-card__copy">{error}</p>
              </div>
            ) : null}

            {bootstrapStatus === "empty" ? (
              <div className="diagnosis-chat-state-card">
                <p className="diagnosis-chat-state-card__title">暂无诊断会话</p>
                <p className="diagnosis-chat-state-card__copy">请前往告警页面选择一条告警并发起诊断。</p>
              </div>
            ) : null}

            {bootstrapStatus === "ready" && traceStatus === "empty" ? (
              <div className="diagnosis-chat-state-card">
                <p className="diagnosis-chat-state-card__title">会话已创建，等待推理轨迹</p>
                <p className="diagnosis-chat-state-card__copy">当前会话尚未产出 Thinking Trace，可稍后刷新或从告警页重新触发诊断。</p>
              </div>
            ) : null}

            {bootstrapStatus === "ready" && !isLoadingSession && !bubbleItems.length && !thinkingRounds.length && !sessionCards.length ? (
              <div className="diagnosis-chat-state-card">
                <p className="diagnosis-chat-state-card__title">暂无消息</p>
                <p className="diagnosis-chat-state-card__copy">当前会话还没有可展示的对话内容。</p>
              </div>
            ) : null}

            {thinkingRounds.length > 0 ? (
              <ThinkingLoopVisualization
                rounds={thinkingRounds}
                diagnosisResult={session?.diagnosis_result ?? null}
                effectivePlan={effectivePlan}
              />
            ) : sessionCards.length > 0 ? (
              <div className="diagnosis-session-card-list">
                {sessionCards.map((card) => renderSessionCard(card))}
              </div>
            ) : null}

            <Bubble.List autoScroll className="diagnosis-bubble-list" items={bubbleItems} role={bubbleRoles} />

            <div className="diagnosis-chat-state-card">
              <p className="diagnosis-chat-state-card__title">修复审批</p>
              <p className="diagnosis-chat-state-card__copy">可直接点击“修改方案”，未输入时将使用默认优化指令。</p>
              <p className="diagnosis-chat-state-card__copy">
                当前状态：{session?.status ?? "unknown"}，当前版本：v{currentPlanVersion ?? "-"}，最新版本：v
                {latestPlanVersion ?? "-"}，已审批版本：v{approvedPlanVersion ?? "-"}。
              </p>
              {approvalBlockReason ? <p className="diagnosis-chat-state-card__copy">{approvalBlockReason}</p> : null}
              {hasPlan && effectivePlan
                ? renderPlanDetails(effectivePlan)
                : <p className="diagnosis-chat-state-card__copy">{planMissingReason}</p>}
              <textarea
                className="diagnosis-plan-instruction"
                disabled={isRevisingPlan || isApprovingPlan}
                onChange={(event) => setPlanInstruction(event.target.value)}
                placeholder="例如：把第2步改为先观察再执行，观察窗口30秒。"
                rows={3}
                value={planInstruction}
              />
              <div className="diagnosis-sender-actions">
                <Button
                  disabled={isRevisingPlan || isApprovingPlan}
                  loading={isRevisingPlan}
                  onClick={() => {
                    const text = planInstruction.trim();
                    setPlanInstruction("");
                    void revisePlan(text);
                  }}
                >
                  修改方案
                </Button>
                <Button
                  danger
                  disabled={!canApprove || isApprovingPlan || isRevisingPlan}
                  onClick={() => {
                    void approvePlan(false);
                  }}
                >
                  拒绝
                </Button>
                <Button
                  disabled={!canApprove || isApprovingPlan || isRevisingPlan}
                  loading={isApprovingPlan}
                  onClick={() => {
                    void approvePlan(true);
                  }}
                  type="primary"
                >
                  审批通过
                </Button>
              </div>
              {!planInstruction.trim() ? (
                <p className="diagnosis-chat-state-card__copy">
                  未填写指令时，将使用默认优化指令：
                  {effectiveReviseInstruction ?? "请优化当前修复方案，补充更稳妥步骤与验证"}
                </p>
              ) : null}
            </div>

            <div className="diagnosis-chat-composer">
              <Sender
                autoSize={{ minRows: 2, maxRows: 6 }}
                className="diagnosis-sender"
                disabled={senderDisabled}
                footer={
                  <div className="diagnosis-sender-footer">
                    <p className="diagnosis-chat-composer__hint">
                      按 Enter 发送追问。实时事件会继续通过 WebSocket 被消费。
                      {chatContextApplied
                        ? ` 当前会话上下文已注入（trace片段 ${String(traceStepsUsed)} 条）。`
                        : " 尚未注入会话上下文，请确认会话有效后重试。"}
                    </p>
                  </div>
                }
                loading={isSendingMessage}
                onChange={(value) => setDraft(value)}
                onSubmit={handleSubmit}
                placeholder={activeSessionId ? "继续追问当前诊断" : "请先选择一个诊断会话"}
                submitType="enter"
                suffix={
                  <div className="diagnosis-sender-actions">
                    <Dropdown menu={demoMenu} placement="topRight" trigger={["click"]}>
                      <Button className="diagnosis-demo-trigger">模拟演示</Button>
                    </Dropdown>
                    <Button
                      className="diagnosis-send-trigger"
                      disabled={senderDisabled || !draft.trim() || isSendingMessage}
                      onClick={() => handleSubmit(draft)}
                      type="primary"
                    >
                      发送
                    </Button>
                  </div>
                }
                value={draft}
              />
            </div>
          </SurfaceCard>
        </div>
      </div>
    </div>
  );
}

export default DiagnosisPage;
















