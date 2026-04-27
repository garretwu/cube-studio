import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import type { TopologyLayer, TopologyObject, TopologyObjectType, TopologyRelation } from "../api/types";
import TopologyCanvas from "../features/topologyExplorer/components/TopologyCanvas";
import "../features/topologyExplorer/topologyExplorer.css";
import { AppButton, StatusChip } from "../components/ui";
import { formatTopologyStatus, formatTopologyType, getLocationLabel, getStatusTone } from "../features/topologyExplorer/formatters";
import { buildTopologyObjectPath } from "../features/topologyExplorer/topologyObjectRoute";
import type { TopologyCanvasNodeAction } from "../features/topologyExplorer/components/TopologyCanvas";

import type {
  DiagnosisModifiedCandidateChangeView,
  DiagnosisModifiedConfidenceView,
  DiagnosisModifiedContextNodeView,
  DiagnosisModifiedContextView,
  DiagnosisModifiedHypothesesView,
  DiagnosisModifiedProgressView,
  DiagnosisModifiedRemediationKeyView,
  DiagnosisModifiedReportFact,
  DiagnosisModifiedReportView,
  DiagnosisModifiedRootCauseView,
  DiagnosisModifiedStageView,
  DiagnosisModifiedVerificationView,
  ReportTone,
} from "./diagnosisModifiedReportModel";

function cn(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function ReportBadge({
  label,
  tone = "neutral",
  active = false,
}: {
  label: string;
  tone?: ReportTone;
  active?: boolean;
}) {
  return (
    <span
      className={cn(
        "diagnosis-modified-badge",
        `diagnosis-modified-badge--${tone}`,
        active && "diagnosis-modified-badge--active",
      )}
    >
      {label}
    </span>
  );
}

function ReportBadgeButton({
  label,
  tone = "neutral",
  active = false,
  onClick,
}: {
  label: string;
  tone?: ReportTone;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={cn(
        "diagnosis-modified-badge",
        `diagnosis-modified-badge--${tone}`,
        active && "diagnosis-modified-badge--active",
      )}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  );
}

export function ReportSection({
  title,
  description,
  titleHelp,
  children,
}: {
  title: string;
  description?: string;
  titleHelp?: string;
  children: ReactNode;
}) {
  return (
    <section className="diagnosis-modified-report-rail__section">
      <header className="diagnosis-modified-report-rail__section-header">
        <div>
          <h3 className="diagnosis-modified-report-rail__section-label">
            <span>{title}</span>
            {titleHelp ? (
              <span
                aria-label={titleHelp}
                className="diagnosis-modified-report-rail__section-help"
                role="img"
                title={titleHelp}
              >
                ?
              </span>
            ) : null}
          </h3>
          {description ? <p className="diagnosis-modified-report-rail__section-description">{description}</p> : null}
        </div>
      </header>
      <div className="diagnosis-modified-report-rail__section-body">{children}</div>
    </section>
  );
}

export function SectionLoading({
  copy = "Current information is being generated and will sync automatically when ready.",
  variant = "card",
}: {
  copy?: string;
  variant?: "card" | "graph" | "list";
}) {
  const skeleton =
    variant === "graph" ? (
      <article className="diagnosis-modified-report-rail__loading-card diagnosis-modified-report-rail__loading-card--graph">
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--section" />
        <span className="diagnosis-modified-report-rail__loading-block diagnosis-modified-report-rail__loading-block--graph" />
      </article>
    ) : variant === "list" ? (
      <article className="diagnosis-modified-report-rail__loading-card diagnosis-modified-report-rail__loading-card--list">
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--section" />
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--long" />
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--medium" />
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--long" />
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--medium" />
      </article>
    ) : (
      <article className="diagnosis-modified-report-rail__loading-card">
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--section" />
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--long" />
        <span className="diagnosis-modified-report-rail__loading-line diagnosis-modified-report-rail__loading-line--medium" />
      </article>
    );

  return (
    <div
      className="diagnosis-modified-report-rail__section-loading"
      data-testid="diagnosis-modified-report-section-loading"
      aria-live="polite"
    >
      <div className="diagnosis-modified-report-rail__loading-skeleton" aria-hidden="true">
        {skeleton}
      </div>
      <p>{copy}</p>
    </div>
  );
}

export function ReportOverview({
  view,
}: {
  view: DiagnosisModifiedReportView["overview"];
}) {
  type MetaItem = {
    key: string;
    label: string;
    value: string;
    title: string;
    tone?: ReportTone;
  };

  const metaItems: MetaItem[] = view.isDefaultPreview
    ? view.severityLabel
      ? [
          {
            key: "severity",
            label: "Severity",
            value: view.severityLabel,
            title: view.severityLabel,
            tone: "warning",
          },
        ]
      : []
    : [
        {
          key: "alert",
          label: "Alert",
          value: view.alertName || "--",
          title: view.alertName || "--",
        },
        {
          key: "updated",
          label: "Updated",
          value: view.updatedAt || "--",
          title: view.updatedAt || "--",
        },
      ];

  return (
    <header
      className="diagnosis-modified-report-rail__report-header"
      data-testid="diagnosis-modified-report-header"
    >
      <div
        className="diagnosis-modified-report-rail__report-overview"
        data-testid="diagnosis-modified-report-overview"
      >
        <div className="diagnosis-modified-report-rail__report-layout">
          <div className="diagnosis-modified-report-rail__report-main">
            <div className="diagnosis-modified-report-rail__report-kicker-row">
              <p className="diagnosis-modified-report-rail__eyebrow">Analysis Report</p>
            </div>

            <div className="diagnosis-modified-report-rail__report-headline">
              <h2 className="diagnosis-modified-report-rail__title">{view.title}</h2>
              <p className="diagnosis-modified-report-rail__subtitle" title={view.subtitle || "--"}>
                {view.subtitle || "--"}
              </p>
            </div>
          </div>

          <div
            className="diagnosis-modified-report-rail__inline-meta"
            data-testid="diagnosis-modified-report-inline-meta"
          >
            <span className="diagnosis-modified-report-rail__inline-meta-item diagnosis-modified-report-rail__inline-meta-item--status">
              <ReportBadge active={view.status.isActive} label={view.status.label} tone={view.status.tone} />
            </span>
            {metaItems.map((item) => (
              <span
                className={cn(
                  "diagnosis-modified-report-rail__inline-meta-item",
                  item.key === "alert" && "diagnosis-modified-report-rail__inline-meta-item--alert",
                  item.tone && `diagnosis-modified-report-rail__inline-meta-item--${item.tone}`,
                )}
                key={item.key}
              >
                <span className="diagnosis-modified-report-rail__inline-meta-label">{item.label}</span>
                <span className="diagnosis-modified-report-rail__inline-meta-value" title={item.title}>
                  {item.value}
                </span>
              </span>
            ))}
          </div>
        </div>
      </div>
    </header>
  );
}

export type ReportEmptyPreviewModuleView = {
  key: "context" | "hypotheses" | "rootCause";
  title: string;
  description?: string;
  lines: number;
};

export const DEFAULT_REPORT_PREVIEW_MODULES: ReportEmptyPreviewModuleView[] = [
  {
    key: "context",
    title: "诊断拓扑信息",
    lines: 2,
  },
  {
    key: "hypotheses",
    title: "候选假设验证",
    lines: 2,
  },
  {
    key: "rootCause",
    title: "根因结论和修复方案",
    description: "用于沉淀最终根因判断，并关联后续修复结论。",
    lines: 2,
  },
] as const;

export function ReportEmptyPreviewModule({
  title,
  description,
  lines = 2,
  index = 0,
  dataTestId = "diagnosis-modified-report-empty-preview-module",
}: {
  title: string;
  description?: string;
  lines?: number;
  index?: number;
  dataTestId?: string;
}) {
  return (
    <section
      className="diagnosis-modified-report-rail__empty-preview-module"
      data-testid={dataTestId}
      style={{ animationDelay: `${index * 45}ms` }}
    >
      <div className="diagnosis-modified-report-rail__empty-preview-copy">
        <h3 className="diagnosis-modified-report-rail__empty-preview-title">{title}</h3>
        {description ? (
          <p className="diagnosis-modified-report-rail__empty-preview-description">
            {description} 等待该部分诊断结果输出后展示。
          </p>
        ) : null}
      </div>
      <div
        className="diagnosis-modified-report-rail__loading-progress"
        data-testid={`${dataTestId}-progress`}
        aria-hidden="true"
      >
        {Array.from({ length: Math.max(1, lines) }, (_, lineIndex) => (
          <span
            className={cn(
              "diagnosis-modified-report-rail__loading-progress-line",
              lineIndex === 0
                ? "diagnosis-modified-report-rail__loading-progress-line--primary"
                : "diagnosis-modified-report-rail__loading-progress-line--secondary",
            )}
            data-lines={lines}
            key={`loading-line-${lineIndex}`}
          />
        ))}
      </div>
    </section>
  );
}

export function ReportEmptyPreview() {
  return (
    <div className="diagnosis-modified-report-rail__empty-preview" data-testid="diagnosis-modified-report-empty-preview">
      {DEFAULT_REPORT_PREVIEW_MODULES.map((module, index) => (
        <ReportEmptyPreviewModule
          dataTestId="diagnosis-modified-report-empty-preview-module"
          description={module.description}
          key={module.title}
          lines={module.lines}
          title={module.title}
          index={index}
        />
      ))}
    </div>
  );
}

export function ReportProgressSection({ progress }: { progress: DiagnosisModifiedProgressView }) {
  return (
    <div className="diagnosis-modified-report-rail__progress" data-testid="diagnosis-modified-report-progress">
      {progress.steps.map((step, index) => (
        <article
          className={cn(
            "diagnosis-modified-report-rail__progress-step",
            `diagnosis-modified-report-rail__progress-step--${step.status}`,
          )}
          data-status={step.status}
          data-testid={`diagnosis-modified-progress-step-${step.id}`}
          key={step.id}
        >
          <div className="diagnosis-modified-report-rail__progress-step-index" aria-hidden="true">
            {step.status === "completed" ? "OK" : step.status === "active" ? "IN" : String(index + 1).padStart(2, "0")} 
          </div>
          <div className="diagnosis-modified-report-rail__progress-step-copy">
            <strong>{step.title}</strong>
            <p>{step.summary}</p>
          </div>
        </article>
      ))}
    </div>
  );
}

function LevelBlock({
  title,
  kicker,
  children,
}: {
  title: string;
  kicker?: string;
  children: ReactNode;
}) {
  return (
    <article className="diagnosis-modified-report-rail__level-block">
      <header className="diagnosis-modified-report-rail__level-block-header">
        {kicker ? <p className="diagnosis-modified-report-rail__level-block-kicker">{kicker}</p> : null}
        <h4 className="diagnosis-modified-report-rail__level-block-title">{title}</h4>
      </header>
      <div className="diagnosis-modified-report-rail__level-block-body">{children}</div>
    </article>
  );
}

export function SessionLevelSection({
  context,
}: {
  context: DiagnosisModifiedContextView;
}) {
  return <DiagnosisContextSection context={context} />;
}

export function HypothesisLevelSection({
  hypotheses,
}: {
  hypotheses: DiagnosisModifiedHypothesesView;
}) {
  return (
    <div className="diagnosis-modified-report-rail__level-stack">
      <HypothesisSection hypotheses={hypotheses} />
    </div>
  );
}

export function RootCauseLevelSection({
  candidateChanges,
  hypothesesState,
  rootCause,
}: {
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
  hypothesesState: DiagnosisModifiedHypothesesView["state"];
  rootCause: DiagnosisModifiedRootCauseView;
}) {
  const rootCauseItems = rootCause.items ?? [];
  const rootCauseReady = rootCause.state === "ready" && rootCauseItems.length > 0;
  const showCandidateFallback = candidateChanges.length > 0 && hypothesesState !== "ready";

  return (
    <div className="diagnosis-modified-report-rail__level-stack">
      {rootCauseReady ? (
        <>
          <div className="diagnosis-modified-report-rail__stack">
            {rootCauseItems.map((item) => {
              const remediationSummary = String(item.remediation?.detail ?? "").trim();
              const confidenceFact = item.facts.find((fact) => fact.label.trim().toLowerCase() === "confidence");
              return (
                <article className="diagnosis-modified-report-rail__candidate" key={item.id}>
                  <div className="diagnosis-modified-report-rail__candidate-header">
                    <div className="diagnosis-modified-report-rail__candidate-copy">
                      <strong className="diagnosis-modified-report-rail__rootcause-title">
                        <span
                          className="diagnosis-modified-report-rail__rootcause-title-text diagnosis-modified-report-rail__candidate-title"
                          title={item.title}
                        >
                          {item.title}
                        </span>
                        {confidenceFact?.value ? (
                          <ReportBadge label={`置信度 ${confidenceFact.value}`} tone="neutral" />
                        ) : null}
                      </strong>
                      <p
                        className="diagnosis-modified-report-rail__candidate-summary"
                        title={item.summary}
                      >
                        {item.summary}
                      </p>
                      {remediationSummary ? (
                        <div className="diagnosis-modified-report-rail__candidate-remediation">
                          <div className="diagnosis-modified-report-rail__candidate-remediation-header">
                            <div className="diagnosis-modified-report-rail__candidate-remediation-title">
                              <span>修复方案</span>
                              <ReportBadge label="已生成" tone="success" />
                            </div>
                          </div>
                          <p className="diagnosis-modified-report-rail__candidate-remediation-summary">
                            <span className="diagnosis-modified-report-rail__candidate-remediation-summary-label">
                              修复方案总结：
                            </span>
                            <span title={remediationSummary}>{remediationSummary}</span>
                          </p>
                        </div>
                      ) : null}
                    </div>
                  </div>
                  <div className="diagnosis-modified-report-rail__candidate-details">
                    <RemediationKeySection hideConclusion remediation={item.remediation} />
                  </div>
                </article>
              );
            })}
          </div>
          {showCandidateFallback ? (
            <div className="diagnosis-modified-report-rail__level-inset">
              <p className="diagnosis-modified-report-rail__callout-label">Root-cause candidates</p>
              <CandidateChangesSection candidateChanges={candidateChanges} />
            </div>
          ) : null}
        </>
      ) : (
        <SectionLoading copy="Waiting for report data." variant="list" />
      )}
    </div>
  );
}

export function DiagnosisSummarySection({
  confidence,
  candidateChanges,
  conclusion,
  hypotheses,
  progress,
  remediation,
  rootCause,
  stage,
  verification,
}: {
  confidence: DiagnosisModifiedConfidenceView;
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
  conclusion: DiagnosisModifiedReportView["conclusion"];
  hypotheses: DiagnosisModifiedHypothesesView;
  progress: DiagnosisModifiedProgressView;
  remediation: DiagnosisModifiedRemediationKeyView;
  rootCause: DiagnosisModifiedRootCauseView;
  stage: DiagnosisModifiedStageView;
  verification: DiagnosisModifiedVerificationView;
}) {
  const processStatus = resolveSummaryProcessStatus({ progress, remediation, stage });

  return (
    <div className="diagnosis-modified-report-rail__section-stack">
      <SummaryProcessStatus status={processStatus} />
      <HypothesisSection hypotheses={hypotheses} />
      <VerificationSection verification={verification} />
      <ConfidenceSection confidence={confidence} />
      {rootCause.state === "ready" ? (
        <>
          <div className="diagnosis-modified-report-rail__fact-list">
            {conclusion.facts.map((fact) => (
              <FactRow fact={fact} key={fact.label} />
            ))}
          </div>
          {hypotheses.state !== "ready" ? (
            <CandidateChangesSection candidateChanges={candidateChanges} />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function resolveSummaryProcessStatus({
  progress,
  remediation,
  stage,
}: {
  progress: DiagnosisModifiedProgressView;
  remediation: DiagnosisModifiedRemediationKeyView;
  stage: DiagnosisModifiedStageView;
}) {
  if (!stage.isActive && remediation.state === "ready") {
    return {
      text: "Root cause and remediation are ready.",
      blinking: false,
    } as const;
  }

  switch (progress.activeStepId) {
    case "context":
      return {
        text: "Building impact topology context.",
        blinking: true,
      } as const;
    case "hypotheses":
      return {
        text: "Generating candidate hypotheses.",
        blinking: true,
      } as const;
    case "verification":
      return {
        text: "Validating evidence against hypotheses.",
        blinking: true,
      } as const;
    case "confidence":
      return {
        text: "Updating confidence across candidates.",
        blinking: true,
      } as const;
    case "remediation":
      return {
        text: "Generating remediation guidance.",
        blinking: true,
      } as const;
    default:
      return {
        text: "Diagnosis is in progress.",
        blinking: true,
      } as const;
  }
}

function SummaryProcessStatus({
  status,
}: {
  status: { text: string; blinking: boolean };
}) {
  return (
    <p
      className={cn(
        "diagnosis-modified-report-rail__process-status",
        status.blinking && "diagnosis-modified-report-rail__process-status--blinking",
      )}
      data-testid="diagnosis-modified-summary-process-status"
    >
      {status.text}
    </p>
  );
}

export function DiagnosisContextSection({ context }: { context: DiagnosisModifiedContextView }) {
  if (context.state !== "ready") {
    return <SectionLoading copy="Waiting for context data." variant="graph" />;
  }

  return (
    <div className="diagnosis-modified-report-rail__context">
      <ContextTopologyGraph context={context} />
    </div>
  );
}

function inferTopologyType(node: DiagnosisModifiedContextNodeView): TopologyObjectType {
  const text = `${node.detail ?? ""} ${node.label}`.toLowerCase();
  if (text.includes("gpu")) {
    return "gpu";
  }
  if (text.includes("switch")) {
    return "switch";
  }
  if (text.includes("port")) {
    return "port";
  }
  if (text.includes("bmc")) {
    return "bmc";
  }
  if (text.includes("rack")) {
    return "rack";
  }
  if (text.includes("cluster")) {
    return "cluster";
  }
  if (text.includes("pod")) {
    return "pod";
  }
  if (text.includes("service") || text.includes("svc") || text.includes("api")) {
    return "service";
  }
  return "node";
}

function inferTopologyLayer(type: TopologyObjectType): TopologyLayer {
  if (type === "switch" || type === "port") {
    return "network";
  }
  if (type === "service") {
    return "service";
  }
  if (type === "rack") {
    return "physical";
  }
  return "compute";
}

function mapContextNodeToTopologyNode(node: DiagnosisModifiedContextNodeView): TopologyObject {
  const type = inferTopologyType(node);
  const detail = node.detail?.trim() || node.label;

  return {
    id: node.id,
    name: node.label,
    type,
    status: node.role === "problem" ? "abnormal" : "impacted",
    layer: inferTopologyLayer(type),
    domain: "diagnosis",
    region: "global",
    zone: "global",
    summary: detail,
    tags: [node.role],
    updatedAt: new Date(0).toISOString(),
    attributes: {
      source: "diagnosis_context",
      role: node.role,
      rawDetail: detail,
    },
  };
}

function mapContextEdgeToTopologyRelation(
  edge: DiagnosisModifiedContextView["graph"]["edges"][number],
  nodeRoleMap: Map<string, DiagnosisModifiedContextNodeView["role"]>,
): TopologyRelation {
  const sourceRole = nodeRoleMap.get(edge.sourceId);

  return {
    id: edge.id,
    source: edge.sourceId,
    target: edge.targetId,
    relationType: "depends_on",
    status: "healthy",
    isCritical: false,
    impactLevel: sourceRole === "problem" ? "medium" : "low",
    label: edge.label,
  };
}

function normalizeDiagnosisContextPopoverText(value: string, { maxLength = 96 }: { maxLength?: number } = {}) {
  const cleaned = String(value ?? "")
    .replace(/\b(?:svc:service:|service:|entity:|ns:|pod:|node:|gpu:|proc:|process:)\b/giu, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) {
    return "";
  }
  if (cleaned.length <= maxLength) {
    return cleaned;
  }
  return `${cleaned.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`;
}

function buildDiagnosisContextLocationText(node: TopologyObject) {
  const readable = normalizeDiagnosisContextPopoverText(getLocationLabel(node) || "", { maxLength: 56 });
  if (readable && readable !== "global / global") {
    return readable;
  }
  const attrs = node.attributes as Record<string, unknown>;
  const zone = normalizeDiagnosisContextPopoverText(String(attrs.zone ?? attrs.namespace ?? attrs.node ?? ""), { maxLength: 56 });
  return zone || "未提供";
}

function ContextTopologyGraph({ context }: { context: DiagnosisModifiedContextView }) {
  const navigate = useNavigate();
  const [nodeActions, setNodeActions] = useState<TopologyCanvasNodeAction | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const isNodeHoveringRef = useRef(false);
  const isPopoverHoveringRef = useRef(false);
  const topologyNodes = context.graph.nodes.map(mapContextNodeToTopologyNode);
  const nodeRoleMap = new Map(context.graph.nodes.map((node) => [node.id, node.role] as const));
  const topologyEdges = context.graph.edges.map((edge) => mapContextEdgeToTopologyRelation(edge, nodeRoleMap));
  const actionNode = nodeActions?.node;
  const actionPosition = useMemo(
    () =>
      nodeActions
        ? {
            left: Math.min(nodeActions.clientX + 16, window.innerWidth - 360),
            top: Math.max(nodeActions.clientY - 24, 92),
          }
        : null,
    [nodeActions],
  );

  const clearCloseTimer = () => {
    if (closeTimerRef.current != null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const closePopover = () => {
    clearCloseTimer();
    setNodeActions(null);
  };

  const scheduleClose = () => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => {
      if (isNodeHoveringRef.current || isPopoverHoveringRef.current) {
        return;
      }
      setNodeActions(null);
      closeTimerRef.current = null;
    }, 150);
  };

  useEffect(() => {
    return () => {
      clearCloseTimer();
    };
  }, []);

  return (
    <div
      className="diagnosis-modified-report-rail__context-graph topology-route topology-route--modified"
      aria-label="诊断上下文拓扑图"
    >
      <TopologyCanvas
        edges={topologyEdges}
        forceEdgeLabels={false}
        hoverInfoTooltipEnabled={false}
        layoutPreset="layered"
        matchedNodeIds={[]}
        neighborDepths={new Map()}
        nodes={topologyNodes}
        nativeTitleEnabled={false}
        nodeActionOpenMode="hover-and-click"
        onCanvasInteraction={() => {
          isNodeHoveringRef.current = false;
          isPopoverHoveringRef.current = false;
          closePopover();
        }}
        onHoverNode={(nodeId) => {
          if (nodeId) {
            isNodeHoveringRef.current = true;
            clearCloseTimer();
            return;
          }
          isNodeHoveringRef.current = false;
          if (!isPopoverHoveringRef.current) {
            scheduleClose();
          }
        }}
        onOpenNodeActions={(payload) => {
          clearCloseTimer();
          setNodeActions(payload);
        }}
        onSelectNode={() => undefined}
        variant="modified"
      />
      {actionNode && actionPosition ? (
        <aside
          className="topology-node-menu topology-node-menu--diagnosis"
          data-testid="diagnosis-context-node-popover"
          onMouseEnter={() => {
            isPopoverHoveringRef.current = true;
            clearCloseTimer();
          }}
          onMouseLeave={() => {
            isPopoverHoveringRef.current = false;
            if (!isNodeHoveringRef.current) {
              scheduleClose();
            }
          }}
          style={actionPosition}
        >
          <div className="topology-node-menu__header">
            <div>
              <p className="topology-node-menu__eyebrow">{formatTopologyType(actionNode.type)}</p>
              <h3 className="topology-node-menu__title">
                {normalizeDiagnosisContextPopoverText(actionNode.name, { maxLength: 56 }) || actionNode.name}
              </h3>
            </div>
            <StatusChip tone={getStatusTone(actionNode.status)}>{formatTopologyStatus(actionNode.status)}</StatusChip>
          </div>
          <dl className="topology-node-menu__facts">
            <div>
              <dt>位置</dt>
              <dd>{buildDiagnosisContextLocationText(actionNode)}</dd>
            </div>
            <div>
              <dt>摘要</dt>
              <dd className="topology-node-menu__summary--diagnosis">
                {normalizeDiagnosisContextPopoverText(actionNode.summary, { maxLength: 120 })}
              </dd>
            </div>
          </dl>
          <div className="topology-node-menu__actions">
            <AppButton
              onClick={() => {
                navigate(buildTopologyObjectPath(actionNode.id, "default"));
                closePopover();
              }}
              size="sm"
              variant="primary"
            >
              查看拓扑
            </AppButton>
          </div>
          <p className="topology-node-menu__hint">当前对象已匹配，点击画布空白区域可关闭此浮层。</p>
        </aside>
      ) : null}
    </div>
  );
}

export function RootCauseAssessmentSection({
  candidateChanges,
  rootCause,
}: {
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
  rootCause: DiagnosisModifiedRootCauseView;
}) {
  if (rootCause.state !== "ready") {
    return <SectionLoading copy="Waiting for report data." variant="list" />;
  }

  return <CandidateChangesSection candidateChanges={candidateChanges} />;
}

function FactRow({ fact }: { fact: DiagnosisModifiedReportFact }) {
  return (
    <div className="diagnosis-modified-report-rail__fact-row">
      <span>{fact.label}</span>
      <strong>{fact.value}</strong>
    </div>
  );
}

function getCandidateCertaintyMeta(label: string | undefined, tone: ReportTone) {
  const rawLabel = String(label ?? "").trim();
  const normalized = rawLabel.toLowerCase();

  if (normalized.includes("confirmed") || rawLabel.includes("已确认") || rawLabel.includes("当前根因")) {
    return { label: "已确认", tone: "success" as const };
  }

  if (
    normalized.includes("probable") ||
    rawLabel.includes("高概率") ||
    rawLabel.includes("较大概率") ||
    rawLabel.includes("有可能") ||
    rawLabel.includes("当前候选") ||
    rawLabel.includes("候选")
  ) {
    return { label: "有可能", tone: "warning" as const };
  }

  if (
    normalized.includes("ambiguous") ||
    rawLabel.includes("模糊") ||
    rawLabel.includes("证据不足") ||
    rawLabel.includes("待确认")
  ) {
    return { label: "模糊", tone: "neutral" as const };
  }

  if (tone === "success" || tone === "accent") {
    return { label: "已确认", tone: "success" as const };
  }

  if (tone === "warning") {
    return { label: "有可能", tone: "warning" as const };
  }

  return { label: "模糊", tone: "neutral" as const };
}

function HypothesisSection({
  hypotheses,
}: {
  hypotheses: DiagnosisModifiedHypothesesView;
}) {
  const [detailVisibilityOverrides, setDetailVisibilityOverrides] = useState<Record<string, boolean>>({});
  const [focusTarget, setFocusTarget] = useState<{ itemId: string; group: HypothesisDetailGroupKey } | null>(null);

  if (hypotheses.state !== "ready") {
    return <SectionLoading copy="Waiting for report data." variant="list" />;
  }

  function openDetails(itemId: string) {
    setDetailVisibilityOverrides((previous) => {
      if (previous[itemId] === true) {
        return previous;
      }

      return {
        ...previous,
        [itemId]: true,
      };
    });
  }

  function toggleDetails(itemId: string, currentVisibility: boolean) {
    setDetailVisibilityOverrides((previous) => ({
      ...previous,
      [itemId]: !currentVisibility,
    }));
  }

  function formatHypothesisTitle(title: string) {
    return title.replace(/^\s*假设\s*\d+\s*[:：]\s*/u, "").trim() || title.trim();
  }

  return (
    <div className="diagnosis-modified-report-rail__subsection diagnosis-modified-report-rail__subsection--hypotheses">
      <div className="diagnosis-modified-report-rail__stack">
        {hypotheses.items.map((item) => {
          const defaultExpanded = hypotheses.detailMode === "expanded";
          const showDetails = detailVisibilityOverrides[item.id] ?? defaultExpanded;
          const certainty = getCandidateCertaintyMeta(item.statusLabel, item.tone);
          const evidenceMeta = item.evidenceItems.reduce(
            (acc, entry) => {
              if (entry.kind === "support") {
                acc.support += 1;
              } else if (entry.kind === "against") {
                acc.against += 1;
              } else if (entry.kind === "validation") {
                acc.validation += 1;
              }
              return acc;
            },
            { support: 0, against: 0, validation: 0 },
          );
          return (
            <article
              className="diagnosis-modified-report-rail__candidate"
              data-testid={`diagnosis-modified-hypothesis-card-${item.id}`}
              key={item.id}
            >
              <div className="diagnosis-modified-report-rail__candidate-header diagnosis-modified-report-rail__candidate-header--two-col-hypothesis">
                <div className="diagnosis-modified-report-rail__candidate-copy">
                  <strong
                    className="diagnosis-modified-report-rail__candidate-title"
                    title={formatHypothesisTitle(item.title)}
                  >
                    {formatHypothesisTitle(item.title)}
                  </strong>
                  {item.summary ? (
                    <p
                      className="diagnosis-modified-report-rail__candidate-summary"
                      title={item.summary}
                    >
                      {item.summary}
                    </p>
                  ) : null}
                </div>
                <div className="diagnosis-modified-report-rail__candidate-meta">
                  <div
                    className={cn(
                      "diagnosis-modified-report-rail__certainty-chip",
                      `diagnosis-modified-report-rail__certainty-chip--${certainty.tone}`,
                    )}
                  >
                    <span className="diagnosis-modified-report-rail__certainty-dot" aria-hidden="true" />
                    <ReportBadge label={certainty.label} tone={certainty.tone} />
                    <span className="diagnosis-modified-report-rail__certainty-label">置信度</span>
                    <span className="diagnosis-modified-report-rail__certainty-value">{item.confidenceLabel}</span>
                  </div>
                  {evidenceMeta.support > 0 ? (
                    <ReportBadgeButton
                      label={`支持 ${evidenceMeta.support}`}
                      onClick={() => {
                        openDetails(item.id);
                        setFocusTarget({ itemId: item.id, group: "support" });
                      }}
                      tone="success"
                    />
                  ) : null}
                  {evidenceMeta.against > 0 ? (
                    <ReportBadgeButton
                      label={`反证 ${evidenceMeta.against}`}
                      onClick={() => {
                        openDetails(item.id);
                        setFocusTarget({ itemId: item.id, group: "against" });
                      }}
                      tone="warning"
                    />
                  ) : null}
                  {evidenceMeta.validation > 0 ? (
                    <ReportBadgeButton
                      label={`验证 ${evidenceMeta.validation}`}
                      onClick={() => {
                        openDetails(item.id);
                        setFocusTarget({ itemId: item.id, group: "validation" });
                      }}
                      tone="info"
                    />
                  ) : null}
                  <button
                    aria-expanded={showDetails}
                    className="diagnosis-modified-report-rail__candidate-detail-toggle diagnosis-modified-report-rail__candidate-detail-toggle--inline"
                    onClick={() => toggleDetails(item.id, showDetails)}
                    type="button"
                  >
                    {showDetails ? "收起证据链" : "展开证据链"}
                  </button>
                </div>
              </div>
              {showDetails ? (
                <HypothesisDetailSections
                  focusGroup={focusTarget?.itemId === item.id ? focusTarget.group : null}
                  item={item}
                  onFocused={() => setFocusTarget(null)}
                />
              ) : null}
            </article>
          );
        })}
      </div>
    </div>
  );
}

type HypothesisDetailGroupKey = "support" | "against" | "validation" | "confidence";

function HypothesisDetailSections({
  item,
  focusGroup,
  onFocused,
}: {
  item: DiagnosisModifiedHypothesesView["items"][number];
  focusGroup: HypothesisDetailGroupKey | null;
  onFocused: () => void;
}) {
  const supportItems = item.evidenceItems.filter((entry) => entry.kind === "support");
  const againstItems = item.evidenceItems.filter((entry) => entry.kind === "against");
  const validationItems = item.evidenceItems.filter((entry) => entry.kind === "validation");
  const supportRef = useRef<HTMLDivElement | null>(null);
  const againstRef = useRef<HTMLDivElement | null>(null);
  const validationRef = useRef<HTMLDivElement | null>(null);
  const confidenceRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!focusGroup) {
      return;
    }

    const refMap = {
      support: supportRef,
      against: againstRef,
      validation: validationRef,
      confidence: confidenceRef,
    } as const;

    const target = refMap[focusGroup].current;
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      onFocused();
    }
  }, [focusGroup, onFocused]);

  if (
    supportItems.length === 0 &&
    againstItems.length === 0 &&
    validationItems.length === 0 &&
    item.confidenceUpdates.length === 0
  ) {
    return null;
  }

  return (
    <div
      className="diagnosis-modified-report-rail__candidate-details"
      data-testid={`diagnosis-modified-hypothesis-details-${item.id}`}
    >
      {supportItems.length > 0 ? (
        <div ref={supportRef}>
          <HypothesisDetailGroup
            entries={supportItems}
            badgeLabel="支持证据"
          />
        </div>
      ) : null}
      {againstItems.length > 0 ? (
        <div ref={againstRef}>
          <HypothesisDetailGroup
            entries={againstItems}
            badgeLabel="反证"
          />
        </div>
      ) : null}
      {validationItems.length > 0 ? (
        <div ref={validationRef}>
          <HypothesisDetailGroup
            entries={validationItems}
            badgeLabel="进一步验证"
          />
        </div>
      ) : null}
      {item.confidenceUpdates.length > 0 ? (
        <div className="diagnosis-modified-report-rail__candidate-detail-group" ref={confidenceRef}>
          <div className="diagnosis-modified-report-rail__stack">
            {item.confidenceUpdates.map((entry) => (
              <article className="diagnosis-modified-report-rail__feedback" key={entry.id}>
                <div className="diagnosis-modified-report-rail__feedback-header">
                  <ReportBadge label={entry.label} tone={entry.tone} />
                  <time>{entry.timestamp}</time>
                </div>
                <strong>{entry.summary}</strong>
              </article>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function HypothesisDetailGroup({
  entries,
  badgeLabel,
}: {
  entries: Array<{ id: string; summary: string; tone: ReportTone }>;
  badgeLabel: string;
}) {
  return (
    <div className="diagnosis-modified-report-rail__candidate-detail-group">
      <div className="diagnosis-modified-report-rail__stack">
        {entries.map((entry) => (
          <article className="diagnosis-modified-report-rail__feedback" key={entry.id}>
            <div className="diagnosis-modified-report-rail__feedback-header">
              <ReportBadge label={badgeLabel} tone={entry.tone} />
            </div>
            <strong>{entry.summary}</strong>
          </article>
        ))}
      </div>
    </div>
  );
}

function VerificationSection({ verification }: { verification: DiagnosisModifiedVerificationView }) {
  if (verification.state !== "ready") {
    return <SectionLoading copy="Waiting for report data." variant="list" />;
  }

  return (
    <div className="diagnosis-modified-report-rail__subsection">
      <div className="diagnosis-modified-report-rail__subsection-header">
        <p className="diagnosis-modified-report-rail__callout-label">Verification trail</p>
        <span>{verification.summary}</span>
      </div>
      <div className="diagnosis-modified-report-rail__stack">
        {verification.items.map((item) => (
          <article className="diagnosis-modified-report-rail__feedback" key={item.id}>
            <div className="diagnosis-modified-report-rail__feedback-header">
              <ReportBadge label={item.title} tone={item.tone} />
            </div>
            <strong>{item.summary}</strong>
            {item.detail ? <p>{item.detail}</p> : null}
          </article>
        ))}
      </div>
    </div>
  );
}

function ConfidenceSection({ confidence }: { confidence: DiagnosisModifiedConfidenceView }) {
  if (confidence.state !== "ready") {
    return <SectionLoading copy="Waiting for report data." variant="list" />;
  }

  const visibleUpdates = confidence.updates.filter(
    (item) => item.label !== "Confidence update" && item.label !== "Current confidence",
  );

  return (
    <div className="diagnosis-modified-report-rail__subsection">
      <div className="diagnosis-modified-report-rail__subsection-header">
        <p className="diagnosis-modified-report-rail__callout-label">Confidence updates</p>
        <span>{confidence.summary}</span>
      </div>
      <div className="diagnosis-modified-report-rail__stack">
        {visibleUpdates.map((item) => (
          <article className="diagnosis-modified-report-rail__feedback" key={item.id}>
            <div className="diagnosis-modified-report-rail__feedback-header">
              <ReportBadge label={item.label} tone={item.tone} />
              <time>{item.timestamp}</time>
            </div>
            <strong>{item.summary}</strong>
          </article>
        ))}
      </div>
    </div>
  );
}

function CandidateChangesSection({
  candidateChanges,
}: {
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
}) {
  if (candidateChanges.length === 0) {
    return <p className="diagnosis-modified-report-rail__empty">Candidate root causes will appear after convergence.</p>;
  }

  return (
    <div className="diagnosis-modified-report-rail__stack">
      {candidateChanges.map((candidate) => {
        const certainty = getCandidateCertaintyMeta(candidate.statusLabel, candidate.tone);

        return (
          <article className="diagnosis-modified-report-rail__candidate" key={candidate.id}>
            <div className="diagnosis-modified-report-rail__candidate-header">
              <div className="diagnosis-modified-report-rail__candidate-copy">
                <strong
                  className="diagnosis-modified-report-rail__candidate-title"
                  title={candidate.title}
                >
                  {candidate.title}
                </strong>
                <p
                  className="diagnosis-modified-report-rail__candidate-summary"
                  title={candidate.summary}
                >
                  {candidate.summary}
                </p>
              </div>
              <div className="diagnosis-modified-report-rail__candidate-meta">
                <span>{candidate.confidenceLabel}</span>
                <ReportBadge label={certainty.label} tone={certainty.tone} />
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}

export function RemediationKeySection({
  remediation,
  hideConclusion = false,
}: {
  remediation?: DiagnosisModifiedRemediationKeyView;
  hideConclusion?: boolean;
}) {
  if (!remediation) {
    return <SectionLoading copy="Waiting for report data." variant="list" />;
  }

  if (remediation.state === "loading") {
    return <SectionLoading copy="Waiting for report data." variant="list" />;
  }

  if (remediation.state === "empty") {
    return <p className="diagnosis-modified-report-rail__empty">No remediation key points are available yet.</p>;
  }

  const factsToShow = hideConclusion
    ? remediation.facts.filter((fact) => {
        const label = fact.label.trim().toLowerCase();
        return label === "safety" || label === "canary";
      })
    : remediation.facts;

  return (
    <div className="diagnosis-modified-report-rail__remediation">
      {!hideConclusion ? (
        <div className="diagnosis-modified-report-rail__status-block">
          <p className="diagnosis-modified-report-rail__callout-label">修复方案结论</p>
          <strong>{remediation.title}</strong>
          <p>{remediation.detail}</p>
        </div>
      ) : null}
      <div className="diagnosis-modified-report-rail__fact-list">
        {factsToShow.map((fact) => (
          <FactRow fact={fact} key={fact.label} />
        ))}
      </div>
      {remediation.steps.length > 0 ? (
        <ol className="diagnosis-modified-report-rail__steps">
          {remediation.steps.map((step) => (
            <li key={step.id}>
              <span className="diagnosis-modified-report-rail__step-index">{step.statusLabel}</span>
              <div>
                <strong>{step.title}</strong>
                <p>{step.detail}</p>
              </div>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
