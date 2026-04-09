import { Bubble, Sender, Think } from "@ant-design/x";
import type { BubbleItemType, BubbleListProps } from "@ant-design/x";
import { Button, Collapse, Dropdown } from "antd";
import type { MenuProps } from "antd";
import {
  isValidElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams } from "react-router-dom";

import type { RemediationPlan, WSEvent } from "../api/types";
import { buildBackendWsUrl } from "../api/ws";
import { AppIcon, StatusChip, SurfaceCard } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useDiagnosisStore } from "../store/diagnosisStore";
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
  toolCallText?: string;
  typingStage?: "thinking" | "conclusion" | "tool" | "complete";
  streamingSpeed?: number;
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

const DEMO_STEP_LABELS = [
  { id: "1", title: "确认影响范围" },
  { id: "2", title: "规划观测动作" },
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
  rollbackSummary:
    "步骤 1 支持自动回滚（k8s_uncordon: node-gpu-01）；步骤 2 无自动回滚，保留人工兜底。",
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
  return (
    typeof value === "object" &&
    value !== null &&
    (value as ToolEventPayload).kind === "tool_event"
  );
}

function isThinkingPlanPayload(value: unknown): value is ThinkingPlanPayload {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as ThinkingPlanPayload).kind === "thinking_plan"
  );
}

function isRootCauseCandidatesPayload(
  value: unknown,
): value is RootCauseCandidatesPayload {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as RootCauseCandidatesPayload).kind === "ranked_candidates"
  );
}

function isApprovalPlanPayload(value: unknown): value is ApprovalPlanPayload {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as ApprovalPlanPayload).kind === "approval_plan"
  );
}

function isChatAnswerPayload(value: unknown): value is ChatAnswerPayload {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as ChatAnswerPayload).kind === "chat_answer"
  );
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
        根因：{plan.root_cause} | 优先级：{plan.priority} | 置信度：
        {Math.round((plan.confidence ?? 0) * 100)}%
      </p>
      <p className="diagnosis-chat-state-card__copy">
        方案说明：{plan.description}
      </p>
      <p className="diagnosis-chat-state-card__copy">
        预估影响：{plan.estimated_impact}
      </p>
      {plan.steps.length ? (
        <div className="diagnosis-plan-steps">
          {plan.steps.map((step) => (
            <div
              className="diagnosis-plan-step"
              key={`${plan.plan_id}-${step.step_id}`}
            >
              <p className="diagnosis-chat-state-card__copy">
                步骤 {step.step_id}：{step.description}
              </p>
              <p className="diagnosis-chat-state-card__copy">
                工具：{step.tool}
              </p>
              <p className="diagnosis-chat-state-card__copy">
                验证：{step.verification.method}
                {step.verification.query
                  ? ` | query: ${step.verification.query}`
                  : ""}
                {step.verification.tool
                  ? ` | tool: ${step.verification.tool}`
                  : ""}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="diagnosis-chat-state-card__copy">
          当前计划暂无执行步骤。
        </p>
      )}
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

function getCertaintyLabel(
  certainty: RootCauseCandidatesPayload["diagnosisCertainty"],
) {
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
        <span
          className={`diagnosis-tool-event__name${isLoading ? " diagnosis-tool-event__name--loading" : ""}`}
        >
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
            children: (
              <pre className="diagnosis-tool-event__params">
                {JSON.stringify(payload.toolParams ?? {}, null, 2)}
              </pre>
            ),
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
  onComplete?: () => void;
};

function StreamingBlock({
  text,
  speed = 6,
  mode,
  onComplete,
}: StreamingBlockProps) {
  const [visibleLength, setVisibleLength] = useState(
    mode === "complete" ? text.length : 0,
  );

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

    if (visibleLength >= text.length) {
      onComplete?.();
      return;
    }

    const timer = window.setTimeout(() => {
      setVisibleLength((current) => Math.min(current + 1, text.length));
    }, speed);

    return () => window.clearTimeout(timer);
  }, [mode, onComplete, speed, text.length, visibleLength]);

  const renderedText = text.slice(0, visibleLength);
  const showCursor = mode === "streaming" && visibleLength < text.length;

  return (
    <pre className="diagnosis-thinking-plan__stream">
      {renderedText}
      {showCursor ? (
        <span className="diagnosis-thinking-plan__cursor" aria-hidden="true" />
      ) : null}
    </pre>
  );
}

function ThinkingPlanCard({ payload }: { payload: ThinkingPlanPayload }) {
  const [thinkingExpanded, setThinkingExpanded] = useState(true);
  const [lastStage, setLastStage] = useState(payload.typingStage ?? "complete");
  const typingStage = payload.typingStage ?? "complete";
  const streamSpeed = payload.streamingSpeed ?? 6;
  const isThinkingStage = typingStage === "thinking";
  const showConclusionGroup = typingStage !== "thinking";
  const showToolCall = typingStage === "tool" || typingStage === "complete";
  const toolCallText =
    payload.toolCallText && payload.toolCallText.trim()
      ? payload.toolCallText
      : `将调用工具 ${payload.toolName}
用途：持续收集并验证下一步关键观测证据。`;

  useEffect(() => {
    const currentStage = payload.typingStage ?? "complete";
    if (currentStage === "thinking") {
      setThinkingExpanded(true);
    } else if (lastStage === "thinking") {
      setThinkingExpanded(false);
    }
    setLastStage(currentStage);
  }, [
    lastStage,
    payload.thinkTitle,
    payload.thinkingText,
    payload.typingStage,
  ]);

  return (
    <div className="diagnosis-thinking-plan">
      <Think
        className="diagnosis-thinking-plan__think"
        title={payload.thinkTitle}
        loading={typingStage !== "complete"}
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
                  <p className="diagnosis-thinking-plan__section-title">
                    Thinking
                  </p>
                </div>
                <div className="status-row diagnosis-thinking-plan__section-state">
                  <StatusChip tone={isThinkingStage ? "info" : "neutral"}>
                    {isThinkingStage ? "流式中" : "已完成"}
                  </StatusChip>
                  <Button
                    className="diagnosis-thinking-plan__toggle"
                    onClick={() => setThinkingExpanded((current) => !current)}
                    size="small"
                    type="text"
                  >
                    {thinkingExpanded ? "收起推理" : "展开推理"}
                  </Button>
                </div>
              </div>
              {thinkingExpanded ? (
                <StreamingBlock
                  text={payload.thinkingText}
                  mode={isThinkingStage ? "streaming" : "complete"}
                  speed={streamSpeed}
                />
              ) : (
                <p className="diagnosis-thinking-plan__collapsed-hint">
                  推理已收起
                </p>
              )}
            </div>

            {showConclusionGroup ? (
              <div className="diagnosis-thinking-plan__section diagnosis-thinking-plan__section--resolution">
                <div className="diagnosis-thinking-plan__section-header">
                  <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--answer">
                    <AppIcon name="documentCheck" size={16} />
                  </span>
                  <div className="diagnosis-thinking-plan__section-copy">
                    <p className="diagnosis-thinking-plan__section-title">
                      结论
                    </p>
                  </div>
                  <StatusChip
                    tone={typingStage === "conclusion" ? "info" : "success"}
                  >
                    {typingStage === "conclusion" ? "流式中" : "已完成"}
                  </StatusChip>
                </div>
                <StreamingBlock
                  text={payload.conclusionText}
                  mode={typingStage === "conclusion" ? "streaming" : "complete"}
                  speed={streamSpeed}
                />

                {showToolCall ? (
                  <div className="diagnosis-thinking-plan__tool-call diagnosis-thinking-plan__section diagnosis-thinking-plan__section--tool">
                    <div className="diagnosis-thinking-plan__section-header">
                      <span className="diagnosis-thinking-plan__section-icon diagnosis-thinking-plan__section-icon--tool">
                        <AppIcon name="clipboardTasks" size={16} />
                      </span>
                      <div className="diagnosis-thinking-plan__section-copy">
                        <p className="diagnosis-thinking-plan__section-title">
                          工具调用
                        </p>
                      </div>
                      <div className="status-row diagnosis-thinking-plan__tool-chips">
                        <StatusChip tone="info">计划调用</StatusChip>
                        <StatusChip tone="warning">未执行</StatusChip>
                        <StatusChip
                          tone={typingStage === "tool" ? "info" : "neutral"}
                        >
                          {typingStage === "tool" ? "流式中" : "已完成"}
                        </StatusChip>
                      </div>
                    </div>
                    <StreamingBlock
                      text={toolCallText}
                      mode={typingStage === "tool" ? "streaming" : "complete"}
                      speed={streamSpeed}
                    />
                    <Collapse
                      bordered={false}
                      className="diagnosis-thinking-plan__collapse"
                      ghost
                      items={[
                        {
                          key: "params",
                          label: "查看结构化参数",
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

function renderRootCauseCandidatesCard(payload: RootCauseCandidatesPayload) {
  const defaultActiveKey = payload.candidates.length
    ? [String(payload.candidates[0].rank)]
    : [];

  return (
    <div className="diagnosis-rootcause">
      <div className="diagnosis-rootcause__header">
        <p className="diagnosis-rootcause__title">候选根因</p>
        <span
          className={`diagnosis-rootcause__certainty diagnosis-rootcause__certainty--${payload.diagnosisCertainty}`}
        >
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
                {candidate.hasRecommendedFix
                  ? "可生成待审批方案"
                  : "暂无推荐方案"}
              </p>
            </div>
          ),
        }))}
      />
    </div>
  );
}

function renderApprovalPlanCard(payload: ApprovalPlanPayload) {
  const defaultActiveKey = payload.steps.length
    ? [String(payload.steps[0].stepId)]
    : [];

  return (
    <div className="diagnosis-approval-plan">
      <div className="diagnosis-approval-plan__header">
        <p className="diagnosis-approval-plan__title">待审批方案</p>
        <div className="diagnosis-approval-plan__chips">
          <span className="diagnosis-approval-plan__chip">
            候选根因 #{payload.candidateRank}
          </span>
          <span className="diagnosis-approval-plan__chip diagnosis-approval-plan__chip--certainty">
            诊断确定性：{getCertaintyLabel(payload.diagnosisCertainty)}
          </span>
        </div>
      </div>

      <p className="diagnosis-approval-plan__summary">
        已基于优先级最高的候选根因生成待审批修复方案，请在审批前确认风险边界。
      </p>

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
          目标流量 {formatRatio(payload.canary.targetPercentage)} · 观察{" "}
          {payload.canary.monitorDuration} 秒 · 判定模式
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

      <p className="diagnosis-approval-plan__rollback">
        回滚摘要：{payload.rollbackSummary}
      </p>
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

  const websocketEnabled =
    import.meta.env.VITE_WS_ENABLED === "true" && Boolean(activeSessionId);

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
          if (
            message.id !== toolMessageId ||
            !isToolEventPayload(message.content)
          ) {
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
    const thinkingText = `The user wants me to diagnose a 'KubePodNotReady' alert with critical severity.
The topology blast radius shows no affected entities, which is unusual. Let me start
by gathering evidence about what pods might be not ready in the Kubernetes
cluster.

Let me begin by:

1. Listing pods across namespaces to find not-ready pods
2. Looking for OOMKilled or pending pods
3. Checking service status
4. Querying logs for any errors

Let me start with multiple parallel queries to understand the state of the cluster.`;
    const conclusionText = `I'll start by gathering evidence about the cluster state. Let me query multiple
sources in parallel to identify the affected pods and their conditions.`;
    const toolCallText = `将调用工具 k8s.list_pods
用途：先确认所有命名空间的 Pod 就绪状态并定位异常对象。`;
    const streamingSpeed = 8;
    const thinkingDuration = thinkingText.length * streamingSpeed + 300;
    const conclusionDuration = conclusionText.length * streamingSpeed + 240;
    const toolDuration = toolCallText.length * streamingSpeed + 240;

    setIsExecutingDemoStep(true);

    setDemoMessages((prev) => [
      ...prev,
      {
        id: thinkingMessageId,
        role: "assistant",
        createdAt: now.toISOString(),
        content: {
          kind: "thinking_plan",
          summary: "规划开始：先展示思考流式输出，再逐步吐出结论与工具调用。",
          thinkTitle: "Deep Thinking 正在规划观测动作",
          thinkingText,
          conclusionText,
          toolName: "k8s.list_pods",
          toolParams: { kwargs: { all_namespaces: true } },
          toolCallText,
          streamingSpeed,
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
              checkTarget: "为 Step 3 的关键观察收集做准备。",
              nextToolName: "Step 3: 收集关键观察（按规划依次执行）",
            },
          ],
        },
      },
    ]);

    const updateThinkingPlan = (
      updater: (payload: ThinkingPlanPayload) => ThinkingPlanPayload,
    ) => {
      setDemoMessages((prev) =>
        prev.map((message) => {
          if (
            message.id !== thinkingMessageId ||
            !isThinkingPlanPayload(message.content)
          ) {
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
        summary: "思考内容已流式输出完成，开始流式输出结论。",
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
          summary: "结论已输出，开始流式输出工具调用。",
          typingStage: "tool",
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
            summary:
              "规划完成：观测动作与工具调用顺序已明确，thinking 与结论输出均已结束。",
            typingStage: "complete",
            blink: false,
            items: payload.items.map((item) => ({
              ...item,
              status: "success",
              blink: false,
            })),
          }));

          setDemoActiveStep((prev) =>
            Math.min(prev + 1, DEMO_STEP_LABELS.length),
          );
          setIsExecutingDemoStep(false);
          demoStepTimerRef.current = undefined;
        }, toolDuration);
      }, conclusionDuration);
    }, thinkingDuration);
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
              evidenceSummary:
                "GPU 利用率持续高位，影响集中在单节点，且已有异常 GPU 进程线索。",
              distinguishingVerification:
                "检查 node-gpu-01 的 GPU 进程列表，并观察终止异常进程后 p95 延迟是否回落。",
              hasRecommendedFix: true,
            },
            {
              rank: 2,
              rootCause: "推理分片负载倾斜导致单节点过载",
              rootCauseLayer: "service",
              confidence: 0.54,
              rootCauseEntities: ["svc-vllm", "node-gpu-01"],
              evidenceSummary:
                "单节点长期承压，但暂缺直接证明是调度策略或分片权重异常。",
              distinguishingVerification:
                "核查分片权重、实例负载分布以及最近变更记录。",
              hasRecommendedFix: false,
            },
            {
              rank: 3,
              rootCause: "网络侧瞬时拥塞放大了推理链路时延",
              rootCauseLayer: "network",
              confidence: 0.33,
              rootCauseEntities: ["sw-01", "node-gpu-01"],
              evidenceSummary:
                "网络路径不能完全排除，但当前证据不足以成为首要根因。",
              distinguishingVerification:
                "查看交换机队列深度、ECN 与链路丢包摘要。",
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
        content:
          "已基于候选根因 #1 生成待审批方案，建议先确认影响范围与回滚边界，再进入人工审批。",
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
  const timelineMessages = useMemo<DisplayMessage[]>(() => {
    const traceMessages: DisplayMessage[] = (session?.trace?.steps ?? []).map(
      (entry, index) => {
        const fallbackTs = new Date().toISOString();
        if ("thought" in entry) {
          const step = entry.step ?? index + 1;
          const thought = entry.thought?.trim() || `Step ${step} reasoning`;
          const toolName = entry.tool_name ?? undefined;
          const params =
            entry.tool_params && Object.keys(entry.tool_params).length
              ? `\n参数: ${JSON.stringify(entry.tool_params)}`
              : "";
          return {
            id: `trace-thinking-${step}-${entry.timestamp}`,
            role: "assistant",
            content: `[思考 ${step}] ${thought}${toolName ? `\n工具: ${toolName}` : ""}${params}`,
            createdAt: entry.timestamp || fallbackTs,
            toolName,
          };
        }

        const toolName = entry.tool || "tool_result";
        const resultSummary =
          typeof entry.result === "object" && entry.result !== null
            ? Object.keys(entry.result).slice(0, 3).join(", ")
            : "";
        return {
          id: `trace-tool-${index + 1}-${entry.timestamp}`,
          role: "tool",
          content: {
            kind: "tool_event",
            toolName,
            status: "success",
            stepLabel: `Step ${index + 1}`,
            toolParams: entry.params,
            summaryLines: [
              resultSummary ? `返回字段: ${resultSummary}` : "工具执行完成。",
            ],
          },
          createdAt: entry.timestamp || fallbackTs,
          toolName,
        };
      },
    );

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

    return [...traceMessages, ...chatMessages, ...demoMessages];
  }, [session?.trace?.steps, messages, demoMessages]);

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
          return (
            <div className="diagnosis-bubble__content">
              {String(content ?? "")}
            </div>
          );
        },
        footer: (_, info) =>
          renderBubbleMeta(info?.extraInfo as BubbleMeta | undefined),
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
          return (
            <div className="diagnosis-bubble__content">
              {String(content ?? "")}
            </div>
          );
        },
        footer: (_, info) =>
          renderBubbleMeta(info?.extraInfo as BubbleMeta | undefined),
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
          return (
            <pre className="diagnosis-bubble__content diagnosis-bubble__content--tool">
              {String(content ?? "")}
            </pre>
          );
        },
        footer: (_, info) =>
          renderBubbleMeta(info?.extraInfo as BubbleMeta | undefined),
      },
    }),
    [],
  );

  const senderDisabled = isLoadingSession || !activeSessionId;
  const traceStepsUsed = Number(chatContextMeta?.["trace_steps_used"] ?? 0);

  const handleDemoStepClick = useCallback<NonNullable<MenuProps["onClick"]>>(
    ({ key }) => {
      const clickedStep = Number(key) - 1;
      if (
        !Number.isInteger(clickedStep) ||
        clickedStep !== demoActiveStep ||
        isExecutingDemoStep
      ) {
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
        setDemoActiveStep((prev) =>
          Math.min(prev + 2, DEMO_STEP_LABELS.length),
        );
        return;
      }

      if (clickedStep === 4) {
        runStepFiveRootCauseCandidates();
        return;
      }

      if (clickedStep === 5) {
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
        const stateText = isDone
          ? "已完成"
          : isActiveAndBusy
            ? "执行中..."
            : isActive
              ? "点击推进"
              : "未解锁";

        return {
          key: String(index + 1),
          disabled: !isActive || isActiveAndBusy,
          label: (
            <div
              className={`diagnosis-demo-menu__item diagnosis-demo-menu__item--${stateClassName}`}
            >
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
    <div className="page-grid diagnosis-chat-page">
      <h1 className="visually-hidden">{"\u8bca\u65ad\u5bf9\u8bdd"}</h1>
      <section className="page-stage diagnosis-chat-stage">
        <div className="page-stage__content diagnosis-chat-page__content">
          <div className="status-row diagnosis-chat-page__statusbar">
            <StatusChip tone={connectionState === "open" ? "success" : "info"}>
              WebSocket {connectionState}
            </StatusChip>
            {activeSessionId ? (
              <StatusChip tone="neutral">
                {"\u4f1a\u8bdd "}
                {activeSessionId}
              </StatusChip>
            ) : null}
            {activeSessionId ? (
              <StatusChip tone={chatContextApplied ? "success" : "info"}>
                {"\u4e0a\u4e0b\u6587 "}
                {chatContextApplied
                  ? "\u5df2\u52a0\u8f7d"
                  : "\u5f85\u52a0\u8f7d"}
              </StatusChip>
            ) : null}
          </div>

          <div className="diagnosis-chat-workspace">
            <SurfaceCard
              bodyClassName="diagnosis-chat-shell__body"
              className="page-stage__panel diagnosis-chat-shell"
              variant="panel"
            >
              {isLoadingSession ? (
                <div className="diagnosis-chat-state-card">
                  <p className="diagnosis-chat-state-card__title">
                    正在加载会话
                  </p>
                  <p className="diagnosis-chat-state-card__copy">
                    正在同步当前诊断会话与消息历史。
                  </p>
                </div>
              ) : null}
              {bootstrapStatus === "error" && error ? (
                <div className="diagnosis-chat-state-card diagnosis-chat-state-card--error">
                  <p className="diagnosis-chat-state-card__title">
                    诊断请求失败
                  </p>
                  <p className="diagnosis-chat-state-card__copy">{error}</p>
                </div>
              ) : null}
              {bootstrapStatus === "ready" && error ? (
                <div className="diagnosis-chat-state-card diagnosis-chat-state-card--error">
                  <p className="diagnosis-chat-state-card__title">
                    追问发送失败
                  </p>
                  <p className="diagnosis-chat-state-card__copy">{error}</p>
                </div>
              ) : null}
              {bootstrapStatus === "empty" ? (
                <div className="diagnosis-chat-state-card">
                  <p className="diagnosis-chat-state-card__title">
                    暂无诊断会话
                  </p>
                  <p className="diagnosis-chat-state-card__copy">
                    请前往告警页面选择一条告警并发起诊断。
                  </p>
                </div>
              ) : null}
              {bootstrapStatus === "ready" && traceStatus === "empty" ? (
                <div className="diagnosis-chat-state-card">
                  <p className="diagnosis-chat-state-card__title">
                    会话已创建，等待推理轨迹
                  </p>
                  <p className="diagnosis-chat-state-card__copy">
                    当前会话尚未产出 Thinking
                    Trace，可稍后刷新或从告警页重新触发诊断。
                  </p>
                </div>
              ) : null}
              {bootstrapStatus === "ready" &&
              !isLoadingSession &&
              !bubbleItems.length ? (
                <div className="diagnosis-chat-state-card">
                  <p className="diagnosis-chat-state-card__title">暂无消息</p>
                  <p className="diagnosis-chat-state-card__copy">
                    当前会话还没有可展示的对话内容。
                  </p>
                </div>
              ) : null}
              <Bubble.List
                autoScroll
                className="diagnosis-bubble-list"
                items={bubbleItems}
                role={bubbleRoles}
              />
              <div className="diagnosis-chat-state-card">
                <p className="diagnosis-chat-state-card__title">修复审批</p>
                <p className="diagnosis-chat-state-card__copy">
                  可直接点击“修改方案”，未输入时将使用默认优化指令。
                </p>
                <p className="diagnosis-chat-state-card__copy">
                  当前状态：{session?.status ?? "unknown"}，当前版本：v
                  {currentPlanVersion ?? "-"}，最新版本：v
                  {latestPlanVersion ?? "-"}，已审批版本：v
                  {approvedPlanVersion ?? "-"}。
                </p>
                {approvalBlockReason ? (
                  <p className="diagnosis-chat-state-card__copy">
                    {approvalBlockReason}
                  </p>
                ) : null}
                {hasPlan && session?.diagnosis_result?.recommended_fix ? (
                  renderPlanDetails(session.diagnosis_result.recommended_fix)
                ) : (
                  <p className="diagnosis-chat-state-card__copy">
                    {planMissingReason}
                  </p>
                )}
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
                      void approvePlan({ approved: false, reason: "Legacy diagnosis page rejection" });
                    }}
                  >
                    拒绝
                  </Button>
                  <Button
                    disabled={!canApprove || isApprovingPlan || isRevisingPlan}
                    loading={isApprovingPlan}
                    onClick={() => {
                      void approvePlan({ approved: true });
                    }}
                    type="primary"
                  >
                    审批通过
                  </Button>
                </div>
                {!planInstruction.trim() ? (
                  <p className="diagnosis-chat-state-card__copy">
                    未填写指令时，将使用默认优化指令：
                    {effectiveReviseInstruction ??
                      "请优化当前修复方案，补充更稳妥步骤与验证"}
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
                  placeholder={
                    activeSessionId
                      ? "继续追问当前诊断"
                      : "请先选择一个诊断会话"
                  }
                  submitType="enter"
                  suffix={
                    <div className="diagnosis-sender-actions">
                      <Dropdown
                        menu={demoMenu}
                        placement="topRight"
                        trigger={["click"]}
                      >
                        <Button className="diagnosis-demo-trigger">
                          模拟演示
                        </Button>
                      </Dropdown>
                      <Button
                        className="diagnosis-send-trigger"
                        disabled={
                          senderDisabled || !draft.trim() || isSendingMessage
                        }
                        onClick={() => handleSubmit(draft)}
                        type="primary"
                      >
                        发送
                      </Button>
                    </div>
                  }
                  value={draft}
                />
              </div>{" "}
            </SurfaceCard>
          </div>
        </div>
      </section>
    </div>
  );
}

export default DiagnosisPage;
