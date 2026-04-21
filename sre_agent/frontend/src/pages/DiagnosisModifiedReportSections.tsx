import type { ReactNode } from "react";
import type { TopologyLayer, TopologyObject, TopologyObjectType, TopologyRelation } from "../api/types";
import TopologyCanvas from "../features/topologyExplorer/components/TopologyCanvas";
import "../features/topologyExplorer/topologyExplorer.css";

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
  copy = "当前信息生成中，完成后会自动同步到报告。",
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
  const metaItems = [
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
  conclusion,
  hypothesesState,
  remediation,
  rootCause,
}: {
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
  conclusion: DiagnosisModifiedReportView["conclusion"];
  hypothesesState: DiagnosisModifiedHypothesesView["state"];
  remediation: DiagnosisModifiedRemediationKeyView;
  rootCause: DiagnosisModifiedRootCauseView;
}) {
  const rootCauseReady = rootCause.state === "ready";
  const showCandidateFallback = candidateChanges.length > 0 && hypothesesState !== "ready";

  return (
    <div className="diagnosis-modified-report-rail__level-stack">
      <LevelBlock kicker="Confirmed outcome" title="鏍瑰洜缁撹">
        {rootCauseReady ? (
          <>
            <div className="diagnosis-modified-report-rail__callout">
              <p className="diagnosis-modified-report-rail__callout-label">Confirmed root cause</p>
              <strong>{conclusion.title}</strong>
              <p>{conclusion.summary}</p>
            </div>
            <div className="diagnosis-modified-report-rail__fact-list">
              {conclusion.facts.map((fact) => (
                <FactRow fact={fact} key={fact.label} />
              ))}
            </div>
            {showCandidateFallback ? (
              <div className="diagnosis-modified-report-rail__level-inset">
                <p className="diagnosis-modified-report-rail__callout-label">Root-cause candidates</p>
                <CandidateChangesSection candidateChanges={candidateChanges} />
              </div>
            ) : null}
          </>
        ) : (
          <SectionLoading copy="正在等待根因结论收敛。" variant="list" />
        )}
      </LevelBlock>

      <LevelBlock kicker="Attached remediation" title="瀵瑰簲淇鏂规">
        <RemediationKeySection remediation={remediation} />
      </LevelBlock>
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
      text: "宸茬敓鎴愭牴鍥犲拰淇鏂规",
      blinking: false,
    } as const;
  }

  switch (progress.activeStepId) {
    case "context":
      return {
        text: "正在构建影响拓扑上下文。",
        blinking: true,
      } as const;
    case "hypotheses":
      return {
        text: "已完成影响拓扑，正在生成候选假设。",
        blinking: true,
      } as const;
    case "verification":
      return {
        text: "已生成假设，正在验证置信度。",
        blinking: true,
      } as const;
    case "confidence":
      return {
        text: "已完成验证证据，正在更新置信度。",
        blinking: true,
      } as const;
    case "remediation":
      return {
        text: "已生成置信度，正在生成修复方案。",
        blinking: true,
      } as const;
    default:
      return {
        text: "诊断流程进行中。",
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
    return <SectionLoading copy="正在等待问题节点与受影响节点的上下文。" variant="graph" />;
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

function ContextTopologyGraph({ context }: { context: DiagnosisModifiedContextView }) {
  const topologyNodes = context.graph.nodes.map(mapContextNodeToTopologyNode);
  const nodeRoleMap = new Map(context.graph.nodes.map((node) => [node.id, node.role] as const));
  const topologyEdges = context.graph.edges.map((edge) => mapContextEdgeToTopologyRelation(edge, nodeRoleMap));

  return (
    <div
      className="diagnosis-modified-report-rail__context-graph topology-route topology-route--modified"
      aria-label="璇婃柇涓婁笅鏂囨嫇鎵戝浘"
    >
      <TopologyCanvas
        edges={topologyEdges}
        forceEdgeLabels={false}
        layoutPreset="layered"
        matchedNodeIds={[]}
        neighborDepths={new Map()}
        nodes={topologyNodes}
        onHoverNode={() => undefined}
        onSelectNode={() => undefined}
        variant="modified"
      />
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
    return <SectionLoading copy="正在等待候选根因与证据对比。" variant="list" />;
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

function HypothesisSection({
  hypotheses,
}: {
  hypotheses: DiagnosisModifiedHypothesesView;
}) {
  if (hypotheses.state !== "ready") {
    return <SectionLoading copy="正在等待进入候选假设阶段。" variant="list" />;
  }

  return (
    <div className="diagnosis-modified-report-rail__subsection">
      <div className="diagnosis-modified-report-rail__stack">
        {hypotheses.items.map((item) => (
          <article
            className="diagnosis-modified-report-rail__candidate"
            data-testid={`diagnosis-modified-hypothesis-card-${item.id}`}
            key={item.id}
          >
            <div className="diagnosis-modified-report-rail__candidate-header">
              <div className="diagnosis-modified-report-rail__candidate-copy">
                <strong>{item.title}</strong>
                <p>{item.summary}</p>
              </div>
              <div className="diagnosis-modified-report-rail__candidate-meta">
                <ReportBadge label={item.statusLabel} tone={item.tone} />
                <span>{item.confidenceLabel}</span>
              </div>
            </div>
            {hypotheses.detailMode === "expanded" ? (
              <HypothesisDetailSections item={item} />
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}

function HypothesisDetailSections({
  item,
}: {
  item: DiagnosisModifiedHypothesesView["items"][number];
}) {
  const supportItems = item.evidenceItems.filter((entry) => entry.kind === "support");
  const againstItems = item.evidenceItems.filter((entry) => entry.kind === "against");
  const validationItems = item.evidenceItems.filter((entry) => entry.kind === "validation");

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
        <HypothesisDetailGroup
          entries={supportItems}
          title="支持证据"
        />
      ) : null}
      {againstItems.length > 0 ? (
        <HypothesisDetailGroup
          entries={againstItems}
          title="反证"
        />
      ) : null}
      {validationItems.length > 0 ? (
        <HypothesisDetailGroup
          entries={validationItems}
          title="继续验证"
        />
      ) : null}
      {item.confidenceUpdates.length > 0 ? (
        <div className="diagnosis-modified-report-rail__candidate-detail-group">
          <p className="diagnosis-modified-report-rail__callout-label">置信度变化</p>
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
  title,
}: {
  entries: Array<{ id: string; summary: string; tone: ReportTone }>;
  title: string;
}) {
  return (
    <div className="diagnosis-modified-report-rail__candidate-detail-group">
      <p className="diagnosis-modified-report-rail__callout-label">{title}</p>
      <div className="diagnosis-modified-report-rail__stack">
        {entries.map((entry) => (
          <article className="diagnosis-modified-report-rail__feedback" key={entry.id}>
            <div className="diagnosis-modified-report-rail__feedback-header">
              <ReportBadge label={title} tone={entry.tone} />
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
    return <SectionLoading copy="正在等待验证证据和后续验证动作。" variant="list" />;
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
    return <SectionLoading copy="正在等待置信度变化与收敛。" variant="list" />;
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
    return <p className="diagnosis-modified-report-rail__empty">候选根因会在结论稳定后显示。</p>;
  }

  return (
    <div className="diagnosis-modified-report-rail__stack">
      {candidateChanges.map((candidate) => (
        <article className="diagnosis-modified-report-rail__candidate" key={candidate.id}>
          <div className="diagnosis-modified-report-rail__candidate-header">
            <div className="diagnosis-modified-report-rail__candidate-copy">
              <strong>{candidate.title}</strong>
              <p>{candidate.summary}</p>
            </div>
            <div className="diagnosis-modified-report-rail__candidate-meta">
              <ReportBadge label={candidate.statusLabel} tone={candidate.tone} />
              <span>{candidate.confidenceLabel}</span>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

export function RemediationKeySection({
  remediation,
}: {
  remediation: DiagnosisModifiedRemediationKeyView;
}) {
  if (remediation.state === "loading") {
    return <SectionLoading copy="正在等待修复方案结论。" variant="list" />;
  }

  if (remediation.state === "empty") {
    return <p className="diagnosis-modified-report-rail__empty">当前还没有可收口的修复关键信息。</p>;
  }

  return (
    <div className="diagnosis-modified-report-rail__remediation">
      <div className="diagnosis-modified-report-rail__status-block">
        <p className="diagnosis-modified-report-rail__callout-label">修复方案结论</p>
        <strong>{remediation.title}</strong>
        <p>{remediation.detail}</p>
      </div>
      <div className="diagnosis-modified-report-rail__fact-list">
        {remediation.facts.map((fact) => (
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

