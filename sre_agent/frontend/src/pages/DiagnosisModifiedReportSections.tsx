import type { ReactNode } from "react";

import type {
  DiagnosisModifiedCandidateChangeView,
  DiagnosisModifiedContextNodeView,
  DiagnosisModifiedContextView,
  DiagnosisModifiedExecutionView,
  DiagnosisModifiedFeedbackItem,
  DiagnosisModifiedNextActionView,
  DiagnosisModifiedRemediationKeyView,
  DiagnosisModifiedReportFact,
  DiagnosisModifiedReportView,
  DiagnosisModifiedRootCauseView,
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
                <span
                  className="diagnosis-modified-report-rail__inline-meta-value"
                  title={item.title}
                >
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

export function DiagnosisSummarySection({
  candidateChanges,
  conclusion,
  rootCause,
}: {
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
  conclusion: DiagnosisModifiedReportView["conclusion"];
  rootCause: DiagnosisModifiedRootCauseView;
}) {
  if (rootCause.state !== "ready") {
    return <SectionLoading copy="正在等待根因结论与结论摘要。" variant="list" />;
  }

  return (
    <div className="diagnosis-modified-report-rail__section-stack">
      <div className="diagnosis-modified-report-rail__fact-list">
        {conclusion.facts.map((fact) => (
          <FactRow fact={fact} key={fact.label} />
        ))}
      </div>

      <CandidateChangesSection candidateChanges={candidateChanges} />
    </div>
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

function ContextTopologyGraph({ context }: { context: DiagnosisModifiedContextView }) {
  const problemNodes = context.graph.nodes.filter((node) => node.role === "problem");
  const affectedNodes = context.graph.nodes.filter((node) => node.role === "affected");
  const allProblemNodes = problemNodes.length > 0 ? problemNodes : context.graph.nodes;
  const positioned = new Map<string, { node: DiagnosisModifiedContextNodeView; x: number; y: number }>();
  const height = 190;
  const distribute = (items: DiagnosisModifiedContextNodeView[], x: number) => {
    const gap = height / (items.length + 1);
    items.forEach((node, index) => {
      positioned.set(node.id, { node, x, y: gap * (index + 1) });
    });
  };

  distribute(allProblemNodes, 96);
  distribute(affectedNodes, 370);

  return (
    <div className="diagnosis-modified-report-rail__context-graph" aria-label="诊断上下文拓扑图">
      <svg viewBox="0 0 468 190" role="img">
        <defs>
          <marker id="diagnosis-report-arrow" markerHeight="8" markerWidth="8" orient="auto" refX="7" refY="4">
            <path d="M0,0 L8,4 L0,8 z" />
          </marker>
        </defs>
        {context.graph.edges.map((edge) => {
          const source = positioned.get(edge.sourceId);
          const target = positioned.get(edge.targetId);
          if (!source || !target) {
            return null;
          }
          return (
            <g key={edge.id}>
              <path
                className="diagnosis-modified-report-rail__context-edge"
                d={`M ${source.x + 34} ${source.y} C ${source.x + 110} ${source.y}, ${target.x - 110} ${target.y}, ${target.x - 34} ${target.y}`}
              />
              <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 6}>
                {edge.label}
              </text>
            </g>
          );
        })}
        {Array.from(positioned.values()).map(({ node, x, y }) => (
          <g
            className={`diagnosis-modified-report-rail__context-node diagnosis-modified-report-rail__context-node--${node.role}`}
            key={node.id}
          >
            <circle cx={x} cy={y} r="28" />
            <text x={x} y={y + 48}>
              {node.label}
            </text>
          </g>
        ))}
      </svg>
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

function CandidateChangesSection({
  candidateChanges,
}: {
  candidateChanges: DiagnosisModifiedCandidateChangeView[];
}) {
  if (candidateChanges.length === 0) {
    return <p className="diagnosis-modified-report-rail__empty">候选根因会在结论收敛后显示。</p>;
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
  actionContent,
  execution,
  feedback,
  nextAction,
  remediation,
}: {
  actionContent?: ReactNode;
  execution: DiagnosisModifiedExecutionView;
  feedback: DiagnosisModifiedFeedbackItem[];
  nextAction: DiagnosisModifiedNextActionView;
  remediation: DiagnosisModifiedRemediationKeyView;
}) {
  if (remediation.state === "loading") {
    return <SectionLoading copy="正在等待修复方案、审批或执行反馈。" variant="list" />;
  }

  if (remediation.state === "empty") {
    return <p className="diagnosis-modified-report-rail__empty">当前还没有可收口的修复关键信息。</p>;
  }

  return (
    <div className="diagnosis-modified-report-rail__remediation">
      <ExecutionSection execution={execution} />
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
      <FeedbackSection feedback={feedback} />
      <NextActionSection actionContent={actionContent} nextAction={nextAction} />
    </div>
  );
}

function ExecutionSection({ execution }: { execution: DiagnosisModifiedExecutionView }) {
  return (
    <div className="diagnosis-modified-report-rail__status-block">
      <p className="diagnosis-modified-report-rail__callout-label">修复建议</p>
      <strong>{execution.title}</strong>
      <p>{execution.detail}</p>
      {execution.highlights.length > 0 ? (
        <div className="diagnosis-modified-report-rail__pill-row">
          {execution.highlights.map((item) => (
            <span className="diagnosis-modified-report-rail__pill" key={item}>
              {item}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function FeedbackSection({ feedback }: { feedback: DiagnosisModifiedFeedbackItem[] }) {
  if (feedback.length === 0) {
    return null;
  }

  return (
    <div className="diagnosis-modified-report-rail__stack">
      {feedback.map((item) => (
        <article className="diagnosis-modified-report-rail__feedback" key={item.id}>
          <div className="diagnosis-modified-report-rail__feedback-header">
            <ReportBadge label={item.label} tone={item.tone} />
            <time>{item.timestamp}</time>
          </div>
          <strong>{item.summary}</strong>
          {item.detail ? <p>{item.detail}</p> : null}
        </article>
      ))}
    </div>
  );
}

function NextActionSection({
  actionContent,
  nextAction,
}: {
  actionContent?: ReactNode;
  nextAction: DiagnosisModifiedNextActionView;
}) {
  return (
    <div className="diagnosis-modified-report-rail__next-action">
      <div className="diagnosis-modified-report-rail__status-block diagnosis-modified-report-rail__status-block--compact">
        <p className="diagnosis-modified-report-rail__callout-label">Next Action</p>
        <strong>{nextAction.title}</strong>
        <p>{nextAction.description}</p>
        {nextAction.helper ? <p className="diagnosis-modified-report-rail__helper">{nextAction.helper}</p> : null}
        {actionContent ? <div className="diagnosis-modified-report-rail__action-slot">{actionContent}</div> : null}
      </div>
    </div>
  );
}
