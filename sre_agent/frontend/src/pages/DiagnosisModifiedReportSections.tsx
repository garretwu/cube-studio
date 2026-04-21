import { useEffect, useState, type ReactNode } from "react";
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
  hypothesesState,
  rootCause,
}: {
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
  hypothesesState: DiagnosisModifiedHypothesesView["state"];
  rootCause: DiagnosisModifiedRootCauseView;
}) {
  const rootCauseReady = rootCause.state === "ready";
  const showCandidateFallback = candidateChanges.length > 0 && hypothesesState !== "ready";
  const rootCauseItems = rootCause.items;
  const isMultiRootCause = rootCauseItems.length > 1;
  const rootCauseIdsKey = rootCauseItems.map((item) => item.id).join("|");
  const [expandedRemediationIds, setExpandedRemediationIds] = useState<string[]>([]);

  useEffect(() => {
    if (rootCauseItems.length <= 1) {
      setExpandedRemediationIds(rootCauseItems.map((item) => item.id));
      return;
    }
    setExpandedRemediationIds([]);
  }, [rootCauseIdsKey, rootCauseItems.length]);

  function toggleItem(itemId: string) {
    setExpandedRemediationIds((previous) =>
      previous.includes(itemId) ? previous.filter((id) => id !== itemId) : [...previous, itemId],
    );
  }

  function expandAll() {
    setExpandedRemediationIds(rootCauseItems.map((item) => item.id));
  }

  function collapseAll() {
    setExpandedRemediationIds([]);
  }

  return (
    <div className="diagnosis-modified-report-rail__level-stack">
      <LevelBlock kicker="Confirmed outcome" title="Root Causes & Remediation">
        {rootCauseReady ? (
          <>
            <div className="diagnosis-modified-report-rail__callout">
              <p className="diagnosis-modified-report-rail__callout-label">
                {isMultiRootCause ? "Multi-root-cause view" : "Confirmed root cause"}
              </p>
              <strong>{isMultiRootCause ? "Multi-root-cause convergence" : "Single root-cause convergence"}</strong>
              <p>{rootCause.summary}</p>
            </div>
            {isMultiRootCause ? (
              <div className="diagnosis-modified-report-rail__rootcause-actions">
                <button
                  className="diagnosis-modified-report-rail__candidate-detail-toggle"
                  onClick={expandAll}
                  type="button"
                >
                  Expand all remediation plans
                </button>
                <button
                  className="diagnosis-modified-report-rail__candidate-detail-toggle"
                  onClick={collapseAll}
                  type="button"
                >
                  Collapse all remediation plans
                </button>
              </div>
            ) : null}
            <div className="diagnosis-modified-report-rail__stack">
              {rootCauseItems.map((item) => {
                const isExpanded = expandedRemediationIds.includes(item.id);
                return (
                  <article className="diagnosis-modified-report-rail__candidate" key={item.id}>
                    <div className="diagnosis-modified-report-rail__candidate-header">
                      <div className="diagnosis-modified-report-rail__candidate-copy">
                        <strong>{item.title}</strong>
                        <p>{item.summary}</p>
                      </div>
                      <div className="diagnosis-modified-report-rail__candidate-meta">
                        <ReportBadge
                          label={item.isPrimary ? "Primary root cause" : "Candidate root cause"}
                          tone={item.isPrimary ? "accent" : "neutral"}
                        />
                        {item.rankLabel ? <span>{item.rankLabel}</span> : null}
                      </div>
                    </div>
                    <div className="diagnosis-modified-report-rail__fact-list">
                      {item.facts.map((fact) => (
                        <FactRow fact={fact} key={`${item.id}-${fact.label}`} />
                      ))}
                    </div>
                    <button
                      aria-expanded={isExpanded}
                      className="diagnosis-modified-report-rail__candidate-detail-toggle"
                      onClick={() => toggleItem(item.id)}
                      type="button"
                    >
                      {isExpanded ? "Collapse remediation plan" : "Expand remediation plan"}
                    </button>
                    {isExpanded ? (
                      <div className="diagnosis-modified-report-rail__candidate-details">
                        <RemediationKeySection remediation={item.remediation} />
                      </div>
                    ) : null}
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

function ContextTopologyGraph({ context }: { context: DiagnosisModifiedContextView }) {
  const topologyNodes = context.graph.nodes.map(mapContextNodeToTopologyNode);
  const nodeRoleMap = new Map(context.graph.nodes.map((node) => [node.id, node.role] as const));
  const topologyEdges = context.graph.edges.map((edge) => mapContextEdgeToTopologyRelation(edge, nodeRoleMap));

  return (
    <div
      className="diagnosis-modified-report-rail__context-graph topology-route topology-route--modified"
      aria-label="鐠囧﹥鏌囨稉濠佺瑓閺傚洦瀚囬幍鎴濇禈"
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

function HypothesisSection({
  hypotheses,
}: {
  hypotheses: DiagnosisModifiedHypothesesView;
}) {
  const [expandedCollapsedDetails, setExpandedCollapsedDetails] = useState<string[]>([]);

  if (hypotheses.state !== "ready") {
    return <SectionLoading copy="Waiting for report data." variant="list" />;
  }

  function toggleCollapsedDetails(itemId: string) {
    setExpandedCollapsedDetails((previous) =>
      previous.includes(itemId) ? previous.filter((id) => id !== itemId) : [...previous, itemId],
    );
  }

  return (
    <div className="diagnosis-modified-report-rail__subsection">
      <div className="diagnosis-modified-report-rail__stack">
        {hypotheses.items.map((item) => {
          const showDetails =
            hypotheses.detailMode === "expanded" || expandedCollapsedDetails.includes(item.id);

          return (
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
              {hypotheses.detailMode === "collapsed" ? (
                <button
                  className="diagnosis-modified-report-rail__candidate-detail-toggle"
                  onClick={() => toggleCollapsedDetails(item.id)}
                  type="button"
                >
                  {showDetails ? "Collapse details" : "Expand details"}
                </button>
              ) : null}
              {showDetails ? <HypothesisDetailSections item={item} /> : null}
            </article>
          );
        })}
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
          title="Support evidence"
        />
      ) : null}
      {againstItems.length > 0 ? (
        <HypothesisDetailGroup
          entries={againstItems}
          title="Counter-evidence"
        />
      ) : null}
      {validationItems.length > 0 ? (
        <HypothesisDetailGroup
          entries={validationItems}
          title="Further validation"
        />
      ) : null}
      {item.confidenceUpdates.length > 0 ? (
        <div className="diagnosis-modified-report-rail__candidate-detail-group">
          <p className="diagnosis-modified-report-rail__callout-label">Confidence updates</p>
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
  remediation?: DiagnosisModifiedRemediationKeyView;
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

  return (
    <div className="diagnosis-modified-report-rail__remediation">
      <div className="diagnosis-modified-report-rail__status-block">
        <p className="diagnosis-modified-report-rail__callout-label">淇鏂规缁撹</p>
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



